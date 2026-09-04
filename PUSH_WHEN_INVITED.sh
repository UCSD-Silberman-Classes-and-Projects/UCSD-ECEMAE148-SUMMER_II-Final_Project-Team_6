#!/bin/bash
# Run this the moment the TA adds Farbod97 to the class org.
# It creates the repo with the exact required name and pushes everything.
set -e
ORG=UCSD-Silberman-Classes-and-Projects
NAME=UCSD-ECEMAE148-SUMMER_II-Final_Project-Team_6
cd "$(dirname "$0")"

gh api "user/memberships/orgs/$ORG" --jq '.state' >/dev/null 2>&1 || {
    echo "Not a member of $ORG yet."
    echo "Message the TA your GitHub username (Farbod97) and accept the invite:"
    echo "  https://github.com/orgs/$ORG/invitation"
    exit 1; }

gh repo create "$ORG/$NAME" --public --source=. --remote=origin --push \
  --description "MAE 148 Team 6 - autonomous GPS tree survey rover with on-board Hailo-8 detection and LLM reporting"
echo
echo "Pushed: https://github.com/$ORG/$NAME"
