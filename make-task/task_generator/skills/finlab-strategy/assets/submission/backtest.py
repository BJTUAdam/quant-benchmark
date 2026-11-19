"""实际回测由平台Docker运行器启动。"""
from finlab.sdk import launch


if __name__ == "__main__":
    launch("strategy.py", "params.json")
