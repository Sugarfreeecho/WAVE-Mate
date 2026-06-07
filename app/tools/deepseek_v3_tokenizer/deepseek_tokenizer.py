# pip3 install transformers
# python deepseek_tokenizer.py（工作目录任意；词表与本脚本同目录）
from pathlib import Path

import transformers

chat_tokenizer_dir = str(Path(__file__).resolve().parent)

tokenizer = transformers.AutoTokenizer.from_pretrained(
    chat_tokenizer_dir, trust_remote_code=True
)

result = tokenizer.encode("Hello!")
print(result)
