from PySide6.QtWidgets import QTableWidget
from ..widgets import table_item
from .base import BasePage
class SystemInterceptPage(BasePage):
 def title_text(self): return '拦截审批'
 def _build(self):
  super()._build(); self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(['ID','名称','匹配','动作','启用']); self.content_lay.addWidget(self.table); self.refresh()
 def refresh(self):
  try: rows=self.client.get('/api/intercept/rules'); self.table.setRowCount(0)
  except Exception:return
  for x in rows:
   r=self.table.rowCount(); self.table.insertRow(r)
   for c,v in enumerate([x.get('id'),x.get('name'),x.get('pattern'),x.get('action'),x.get('enabled')]): self.table.setItem(r,c,table_item(v))
