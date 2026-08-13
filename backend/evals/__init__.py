"""SlotFlow 自建 agent 评测集(offline 桩 / smoke / live 真机 三档)。

用法见 evals/README.md。核心思想:复用真实调用链
``build_slotflow_harness_graph(model=…) -> ainvoke -> result["messages"]``,
评测器直接读原生 message(工具调用 + 终答 + 是否回灌思考),
只换 model 一个参数即可在"桩模型(确定性)"与"真模型(真机)"之间切换。
"""
