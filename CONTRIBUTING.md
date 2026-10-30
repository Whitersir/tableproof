# 参与开发

先使用虚构 CSV 复现问题。项目主要由 Python 业务规则、FastAPI 接口、SQLite 存储、模型适配层和原生 JavaScript 界面组成。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/evaluate.py
```

界面变更还应运行浏览器检查。Windows 默认使用 Edge；Linux/macOS 先执行 `python -m playwright install chromium`。

提交应说明触发条件、修改后的行为和真实验证结果。新增统计口径时，请补上人工可核对的例子。不要用模型自己的评价代替正确答案，也不要把模拟服务结果称为真实模型效果。

仓库不接收凭据或真实人员数据。`data/`、`.env`、测试临时目录和本机配置不应提交。

本项目使用 AI 辅助开发。贡献者应理解自己的修改并能够解释验证方式；个人简历中的贡献范围应依据实际参与情况确认。
