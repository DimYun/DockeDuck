#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status.
set -e

# Terminal Colors for UX
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Validate Input Arguments
if [ "$#" -ne 2 ]; then
    echo -e "${RED}🦆 Error: Invalid arguments.${NC}"
    echo -e "Usage:   ${BLUE}./scripts/init_project.sh <template_name> <target_directory>${NC}"
    echo -e "Example: ${BLUE}./scripts/init_project.sh pytorch-lightning ../my-new-ai-startup${NC}"
    echo -e "\nAvailable templates:"
    ls -1 templates/ | sed 's/^/  - /'
    exit 1
fi

TEMPLATE_NAME=$1
TARGET_DIR=$2
TEMPLATE_DIR="templates/${TEMPLATE_NAME}"

# 2. Validate Template Exists
if [ ! -d "${TEMPLATE_DIR}" ]; then
    echo -e "${RED}🦆 Error: Template '${TEMPLATE_NAME}' does not exist.${NC}"
    echo -e "Available templates:"
    ls -1 templates/ | sed 's/^/  - /'
    exit 1
fi

# 3. Prevent Overwriting Existing Directories
if [ -d "${TARGET_DIR}" ]; then
    echo -e "${RED}🦆 Error: Target directory '${TARGET_DIR}' already exists. Aborting to prevent data loss.${NC}"
    exit 1
fi

echo -e "${YELLOW}Scaffolding '${TEMPLATE_NAME}' into '${TARGET_DIR}'...${NC}"

# 4. Copy the self-contained template
cp -r "${TEMPLATE_DIR}" "${TARGET_DIR}"

# 5. DevEx: Automatically copy .env.example to .env so it works out of the box
if [ -f "${TARGET_DIR}/.env.example" ]; then
    cp "${TARGET_DIR}/.env.example" "${TARGET_DIR}/.env"
    echo -e "  ${GREEN}✔${NC} Initialized .env file"
fi

# 6. DevEx: Initialize a fresh git repository in the new project
cd "${TARGET_DIR}"
git init -q
echo -e "  ${GREEN}✔${NC} Initialized fresh git repository"

# 7. Success Output
echo -e "\n${GREEN}🦆 Success! Your non-root Docker environment is ready.${NC}\n"
echo -e "Next steps:"
echo -e "  ${BLUE}cd ${TARGET_DIR}${NC}"
echo -e "  ${BLUE}make help${NC}       # See available commands"
echo -e "  ${BLUE}make build${NC}      # Build your isolated environment"