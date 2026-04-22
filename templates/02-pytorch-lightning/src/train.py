"""
PyTorch Lightning training script boilerplate.
Includes ClearML experiment tracking integration.
"""
import torch
import pytorch_lightning as pl
from clearml import Task


def main():
    """Initialize ClearML task and prepare for Lightning training."""
    # 1. Initialize ClearML task (picks up .env credentials automatically)
    task = Task.init(project_name="DockDuck_Templates", task_name="Lightning Training")

    # Track some dummy hyperparameters to clear the unused variable linter error!
    task.connect({"batch_size": 32, "learning_rate": 1e-3})

    # 2. Print environment info
    print("🚀 DockDuck ML Environment Initialized")
    print(f"PyTorch Version: {torch.__version__}")
    print(f"PyTorch Lightning Version: {pl.__version__}")

    # 3. Add your Lightning training loop here
    print("Ready for training!")


if __name__ == "__main__":
    main()