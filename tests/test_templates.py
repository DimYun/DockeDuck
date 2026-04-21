import os

def test_template_directories_exist():
    templates = ["01-base-cuda", "02-pytorch-lightning", "03-fastapi-service"]
    for t in templates:
        assert os.path.exists(f"templates/{t}"), f"Template {t} is missing"

def test_clearml_config_example_exists():
    path = "templates/02-pytorch-lightning/configs/clearml.conf.example"
    assert os.path.exists(path), f"ClearML config example is missing at {path}"