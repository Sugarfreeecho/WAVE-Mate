"""
generate_key — 生成加密的配置文件

使用方法：
1. 修改下方的 CONFIG 字典，填入你要加密的配置
2. 运行: python app/generate_key.py
3. 生成 config.bin 文件
4. 从 .env 中删除对应的配置行
5. 重启应用
"""

import base64
import json
from pathlib import Path

# ============================================
# 在这里填入你要加密的配置
# ============================================
CONFIG = {
    "OPENAI_API_KEY": "你的API密钥填在这里",
    "EXECUTOR_LLM": "mimo-v2.5-pro",
    "OPENAI_BASE_URL": "https://token-plan-cn.xiaomimimo.com/v1",
}

# 加密密钥（与secret_loader.py中保持一致）
XOR_KEY = b"MyAgent2026Secret"

def encrypt_and_save():
    """加密并保存配置"""
    if CONFIG["OPENAI_API_KEY"] == "你的API密钥填在这里":
        print("[错误] 请先修改 CONFIG 中的 OPENAI_API_KEY！")
        return False
    
    # 转为JSON再加密
    json_str = json.dumps(CONFIG, ensure_ascii=False)
    data_bytes = json_str.encode('utf-8')
    encrypted = bytes([b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data_bytes)])
    encoded = base64.b64encode(encrypted)
    
    # 保存到 config.bin
    output_path = Path(__file__).resolve().parent / "config.bin"
    output_path.write_bytes(encoded)
    
    # 验证
    verify = base64.b64decode(encoded)
    decrypted = bytes([b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(verify)]).decode('utf-8')
    verified_config = json.loads(decrypted)
    
    if verified_config == CONFIG:
        print(f"[成功] 已生成加密文件: {output_path}")
        print(f"[验证] 解密验证通过")
        print(f"[加密的配置项]:")
        for k, v in CONFIG.items():
            print(f"  - {k} = {v[:10]}..." if len(v) > 10 else f"  - {k} = {v}")
        print(f"[提示] 请从 .env 中删除以上配置行，然后重启应用")
        return True
    else:
        print("[错误] 验证失败！")
        return False

if __name__ == "__main__":
    encrypt_and_save()
