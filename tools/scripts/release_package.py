#!/usr/bin/python3

# -----------------------------------------------------------
# Unless explicitly stated otherwise all files in this repository are licensed under the Apache License Version 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2023-Present Datadog, Inc.
# -----------------------------------------------------------

import argparse
import fileinput
import json
import os
import shutil

import git
import github as gh

import update_versions as uv

REPO_ROOT = "../../"
PACKAGE_LOCATION = f"{REPO_ROOT}packages/Datadog.Unity"

def _verify_git_repo(dest: str, version: str, repo_name: str) -> bool:
    repo = git.Repo(dest)
    for remote in repo.remotes:
        remote.fetch()

    if repo.is_dirty():
        print(f"{repo_name} repo is dirty -- please commit or stash any changes.")
        return False

    # Check if the repo has this version tag already
    if version in repo.tags:
        print(f"Package already has version ${version}")
        return False

    return True

def _copy_package_files(dest: str):
    # Remove everything from the destination directory. It'll be okay I promise.
    for path in os.listdir(dest):
        full_path = os.path.join(dest, path)
        # Well, it'll be okay if we don't delete the .git directory
        if path.startswith(".git"):
            continue

        if os.path.isfile(full_path):
            os.remove(full_path)
        elif os.path.isdir(full_path):
            shutil.rmtree(full_path)

    shutil.copytree(
        PACKAGE_LOCATION,
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('Tests', 'Tests.meta')
    )

def _modify_package_version(dest: str, version: str):
    package_path = os.path.join(dest, "package.json")

    with open(package_path, "r") as json_file:
        package_json = json.load(json_file)
        package_json["version"] = version

    with open(package_path, "w") as json_file:
        json.dump(package_json, json_file, indent=2)

def _modify_assembly_info_version(dest: str, version: str):
    assembly_info_path = os.path.join(dest, "Runtime/AssemblyInfo.cs")

    with fileinput.input(assembly_info_path, inplace=True) as f:
        for line in f:
            if line.startswith("[assembly: AssemblyVersion"):
                print(f"[assembly: AssemblyVersion(\"{version}\")]")
            else:
                print(line, end='')

def _update_android_versions(version: str, github_token: str):
    if version is None:
        # Need to get the latest version from Github
        gh_auth = gh.Auth.Token(github_token)
        github = gh.Github(auth=gh_auth)

        repo = github.get_repo("Datadog/dd-sdk-android")
        release = repo.get_latest_release()
        version = release.tag_name
        print(f"Read latest Android SDK release as {version}")

        github.close()

    uv._update_android_version(version)

def _branch(dest: str, branch_name: str):
    repo = git.Repo(dest)

    branch = repo.create_head(branch_name)
    branch.checkout()

def _commit(repo: git.Repo, message: str):
    repo.git.add('--all')
    repo.index.write()
    repo.git.commit('-m', message)


def _commit_and_tag(repo: git.Repo, version: str):
    _commit(repo, f'Publish version {version}')
    repo.create_tag(version)

def _push(repo: git.Repo):
    origin = repo.remote()
    origin.push()

def _add_repo_note(dest: str):
    repo_snippet = ''
    with open("../snippets/deployment_repo.md") as f:
        repo_snippet = f.read()

    readme_path = os.path.join(dest, "README.md")

    with fileinput.input(readme_path, inplace=True) as f:
        for line in f:
            if line.startswith('[//]: # (Repo Note)'):
                print(repo_snippet)
            else:
                print(line, end='')

def _add_version_to_changelog(package_location: str, version: str):
    readme_path = os.path.join(package_location, "CHANGELOG.md")

    found_unreleased = False
    with fileinput.input(readme_path, inplace=True) as f:
        for line in f:
            if line.startswith('## Unreleased'):
                print(f'## {version}')
                found_unreleased = True
            else:
                print(line, end='')

    return found_unreleased

def _create_gh_release(version: str, changelog_path: str, github_token: str):
    release_notes = ''

    found_version = False
    with open(changelog_path) as f:
        for line in f:
            if found_version:
                if line.startswith("##"):
                    # Done
                    break
                else:
                    stripped = line.strip()
                    if len(stripped) > 0:
                        release_notes += stripped + '\n'
            elif line.startswith(f"## {version}"):
                found_version = True

    gh_auth = gh.Auth.Token(github_token)
    github = gh.Github(auth=gh_auth)

    repo = github.get_repo("Datadog/unity-package")
    repo.create_git_release(version, draft=True)


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--version", required=True, help="The version we're publishing")
    arg_parser.add_argument("--ios-version",
                            required=False,
                            help="Update iOS SDK to the specified version before publishing. Defaults to commit in the sub-module.")
    arg_parser.add_argument("--android-version",
                            required=False,
                            help="Update the Android SDK to the specified version before publishing. Defaults to latest Github release.")

    arg_parser.add_argument("--dest", required=True, help="The destination directory to deploy to. This should the publishing git repo.")
    arg_parser.add_argument("--no-commit", help="Don't commit or tag either repo", action="store_true")
    arg_parser.add_argument("--skip-git-checks", help="Don't check for clean git repos", action="store_true")
    arg_parser.add_argument("--skip-manual-verify", help="Don't pause to allow verification of repos. ONLY FOR USE IN CI", action="store_true")
    args = arg_parser.parse_args()

    github_token = os.environ["GITHUB_TOKEN"]
    if github_token is None:
        print(f"GITHUB_TOKEN not set.")
        return False

    if not os.path.isdir(PACKAGE_LOCATION):
        print(f"Could not find package at {PACKAGE_LOCATION}. Are you running from the script's directory?")
        return False

    version = args.version
    if not args.skip_git_checks:
        if not _verify_git_repo(REPO_ROOT, version, 'Source'):
            return False

        if not _verify_git_repo(args.dest, version, 'Destination'):
            return False

    chore_branch = f"chore/release-{version}"
    print(f"Creating prep btanch '{chore_branch}'")
    _branch(REPO_ROOT, chore_branch)
    print("Modifying CHANGELOG")
    if not _add_version_to_changelog(PACKAGE_LOCATION, version):
        print ("🔥 Failed to modify changelog. Are you missing '## Unreleased' changes?")
        return False
    if not args.no_commit:
        print("Committing changes...")
        source_repo = git.Repo(REPO_ROOT)
        _commit(source_repo, f"chore: Update CHANGELOG for release of {version}.")

    branch_name = f"release/{version}"
    print(f"Creating release branch '{branch_name}'")
    _branch(REPO_ROOT, branch_name)
    _modify_package_version(PACKAGE_LOCATION, version)
    _modify_assembly_info_version(PACKAGE_LOCATION, version)
    if args.ios_version:
        print(f'Updating iOS to version {args.ios_version} and rebuilding.')
        uv._update_ios_version(args.ios_version)
    _update_android_versions(args.android_version, github_token)

    if not args.no_commit:
        print(f"Tagging source repo with {version}")
        _commit_and_tag(source_repo, args.version)

    print(f"Copying package files...")
    _copy_package_files(args.dest)
    _add_repo_note(args.dest)
    if not args.no_commit:
        print(f"Committing and tagging version {args.version}")
        dest_repo = git.Repo(args.dest)
        _commit_and_tag(dest_repo, args.version)

    if not args.skip_manual_verify:
        print("Release is ready. Please verify both repos.")
        print("Ready to proceed with release? Type 'yes' to continue.")
        value = str(input())
        if value != 'yes':
            print("Value not 'yes', aborting.")
            return False
    else:
        print("Skipping manual verification because of --skip-manual-verify")

    print("Pushing source repo...")
    _push(source_repo)
    print("Pushing destination repo...")
    _push(dest_repo)

    print("Creating github release in destination repo...")
    dest_changelog = os.path.join(args.dest, 'CHANGELOG.md')
    _create_gh_release(version, dest_changelog, github_token)

    return True


if __name__ == "__main__":
    main()
