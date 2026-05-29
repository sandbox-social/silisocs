#!/bin/bash
set -o errexit
set -o nounset
set -o pipefail

# This is a fork of the official pdsadmin.sh script,
# which first pulls the PDS admin password from Secrets Manager.
# The original is here:
# https://raw.githubusercontent.com/bluesky-social/pds/refs/heads/main/pdsadmin.sh

PDSADMIN_BASE_URL="https://raw.githubusercontent.com/bluesky-social/pds/main/pdsadmin"

# Command to run.
COMMAND="${1:-help}"
shift || true

# Validate that the value of COMMAND is not 'update'
if [[ "${COMMAND}" == "update" ]]; then
  echo "ERROR: Cannot run pdsadmin update"
  echo "ERROR: To update PDS, update the Docker image in this repository, and re-deploy the CDK template"
  exit 1
fi

# Use minimal PDS env file
ADMIN_SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export PDS_ENV_FILE="${ADMIN_SCRIPT_DIR}/pds.env"

# Get the Secrets Manager secret ID from the CloudFormation output value
# of the PDS stack.
PDS_ADMIN_PASSWORD_SECRET_ID="$(aws cloudformation describe-stacks --region us-east-2 --profile org-bluesky --stack-name BlueskyPdsInfra --query 'Stacks[0].Outputs[?OutputKey==`AdminPasswordID`].OutputValue' --output text)"
if [[ -z "${PDS_ADMIN_PASSWORD_SECRET_ID:-}" ]]; then
  echo "ERROR: PDS admin password secret ID not found from CloudFormation stack"
  exit 1
fi

# Get the PDS admin password from Secrets Manager.
PDS_ADMIN_PASSWORD_VALUE="$(aws secretsmanager get-secret-value --profile org-bluesky --region us-east-2 --secret-id $PDS_ADMIN_PASSWORD_SECRET_ID --query SecretString --output text)"
if [[ -z "${PDS_ADMIN_PASSWORD_VALUE:-}" ]]; then
  echo "ERROR: PDS admin password not found"
  exit 1
fi
export PDS_ADMIN_PASSWORD="${PDS_ADMIN_PASSWORD_VALUE}"

# Download the script, if it exists.
SCRIPT_URL="${PDSADMIN_BASE_URL}/${COMMAND}.sh"
SCRIPT_FILE="$(mktemp /tmp/pdsadmin.${COMMAND}.XXXXXX)"

if ! curl --fail --silent --show-error --location --output "${SCRIPT_FILE}" "${SCRIPT_URL}"; then
  echo "ERROR: ${COMMAND} not found"
  exit 2
fi

chmod +x "${SCRIPT_FILE}"
if "${SCRIPT_FILE}" "$@"; then
  rm --force "${SCRIPT_FILE}"
fi