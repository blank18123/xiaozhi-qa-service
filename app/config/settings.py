import os
from app.config.config_loader import load_config


def check_config_file():
    project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "/"
    custom_config = os.path.join(project_dir, "data", ".config.yaml")
    root_config = os.path.join(project_dir, "config.yaml")

    if not os.path.exists(custom_config) and not os.path.exists(root_config):
        raise FileNotFoundError(
            "找不到配置文件。请在项目根目录创建 config.yaml,"
            "或在 data/ 目录下创建 .config.yaml 覆盖默认配置。"
        )
