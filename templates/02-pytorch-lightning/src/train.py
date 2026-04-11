import torch
import pytorch_lightning as pl
from clearml import Task


def main():
    # Initialize ClearML (requires credentials in .env file)
    # task = Task.init(project_name="DockDuck", task_name="Test Setup")

    print(f"PyTorch Version: {torch.__version__}")
    print(f"CUDA Available: {torch.cuda.is_available()}")

    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")


if __name__ == "__main__":
    main()