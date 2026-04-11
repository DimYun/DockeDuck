# =============================================================================
# DockDuck Root Makefile — build/test all templates
# Goals: one-command project creation, CI-friendly
# Usage:
#   make list           # show templates
#   make build-all      # build every image
#   make scaffold T=pytorch-lightning NAME=myproject
# =============================================================================

TEMPLATES := base-python pytorch-lightning fastapi