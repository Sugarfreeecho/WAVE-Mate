"""
secret_loader — 配置加密加载模块

功能：
- 将敏感配置加密存储为 config.bin
- 启动时自动解密加载到环境变量
- 支持加密多个配置项（API密钥、模型名、API地址等）

使用方法：
1. 运行 generate_key.py 生成 config.bin
2. 从 .env 中删除对应的配置行
3. 重启应用，自动从 config.bin 加载
"""

import base64
import json
import os
from pathlib import Path

# 加密密钥（与generate_key.py中保持一致）
XOR_KEY = b"MyAgent2026Secret"

def _get_project_root() -> Path:
    """获取项目根目录（app/的上级）"""
    return Path(__file__).resolve().parent.parent

def _encrypt(data: str) -> bytes:
    """XOR加密 + Base64编码"""
    data_bytes = data.encode('utf-8')
    encrypted = bytes([b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data_bytes)])
    return base64.b64encode(encrypted)

def _decrypt(encrypted_data: bytes) -> str:
    """Base64解码 + XOR解密"""
    encrypted = base64.b64decode(encrypted_data)
    decrypted = bytes([b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(encrypted)])
    return decrypted.decode('utf-8')

def load_encrypted_config() -> dict:
    """从 config.bin 加载加密的配置
    
    返回:
        dict: 解密后的配置字典，如 {"OPENAI_API_KEY": "xxx", "EXECUTOR_LLM": "xxx"}
              如果文件不存在或解密失败返回空字典
    """
    # 查找config.bin文件
    candidates = [
        _get_project_root() / "config.bin",
        Path(__file__).resolve().parent / "config.bin",
    ]
    
    for config_file in candidates:
        if config_file.exists():
            try:
                encrypted_data = config_file.read_bytes()
                json_str = _decrypt(encrypted_data)
                config = json.loads(json_str)
                if isinstance(config, dict):
                    return config
            except Exception:
                continue
    
    return {}

def load_encrypted_api_key() -> str:
    """兼容旧接口：只加载API密钥"""
    config = load_encrypted_config()
    return config.get("OPENAI_API_KEY", "")
