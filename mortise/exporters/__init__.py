"""輸出器：每種格式一個模組，各自把成品文件 StyledDoc「降階」成該格式的物件。

每個模組提供：
    def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile
並在 mortise/stages/s6_export.py 的 EXPORTERS 登記。輸出器之間互不依賴；
格式專屬的東西不得回流到成品文件。
"""
