from PySide6.QtWidgets import QTableWidget,QLineEdit
from ..widgets import table_item
from .base import BasePage
class SystemToolsPage(BasePage):
 def title_text(self): return '工具目录'
 def _build(self):
  super()._build(); self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(['Key','描述','类型','启用']); self.content_lay.addWidget(self.table); self.refresh()
 def refresh(self):
  try: rows=self.client.get('/api/tools'); self.table.setRowCount(0)
  except Exception:return
  for x in rows:
   r=self.table.rowCount(); self.table.insertRow(r)
   for c,v in enumerate([x.get('key'),x.get('description'),x.get('kind'),x.get('enabled')]): self.table.setItem(r,c,table_item(v))
