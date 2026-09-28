"""Business invoice prefix, independent of the monthly sequence and file name."""
from __future__ import annotations


def invoice_number_prefix(company_name: str, fc_name: str) -> str:
    words = fc_name.split()
    if not company_name.strip():
        raise ValueError("收款公司缺少完整名称，不能生成账单编号")
    if not words or any(not word[0].isascii() or not word[0].isalpha() for word in words):
        raise ValueError("中介人姓名须填写完整英文姓名，才能按各单词首字母生成账单编号；请先核对中介人资料")
    return f"{company_name.strip()}-{''.join(word[0].upper() for word in words)}"
