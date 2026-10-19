from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QTableWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QLabel,QDialog,QFormLayout,QPushButton,QSpinBox,QComboBox
from ..widgets import button,table_item
from ..api_client import ApiError
from .base import BasePage

class SystemAgentsPage(BasePage):
    def title_text(self): return 'Agent 管理'
    def _build(self):
        super()._build(); self.add_toolbar(button('＋ 新建 Agent',primary=True)); self.toolbar.itemAt(0).widget().clicked.connect(self._new)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(['Key','名称','角色','启用','运行秒','操作']); self.table.verticalHeader().setVisible(False); self.add_to_content(self.table); self.refresh()
        self.timer=QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(10000)
    def refresh(self):
        try: rows=self.client.get('/api/agents')
        except ApiError:return
        self.table.setRowCount(0)
        for a in rows:
            r=self.table.rowCount(); self.table.insertRow(r)
            for c,v in enumerate([a['key'],a['name'],a.get('role',''),'启用' if a.get('enabled') else '停用',a.get('run_seconds',0)]): self.table.setItem(r,c,table_item(v))
            b=button('编辑',obj='Link'); b.clicked.connect(lambda _,k=a['key']: self._edit(k)); self.table.setCellWidget(r,5,b)
    def _new(self): self._edit(None)
    def _edit(self,key):
        d=QDialog(self); d.setWindowTitle('Agent 配置'); f=QFormLayout(d); vals={}
        old=None
        if key:
            try: old=self.client.get('/api/agents/'+key)
            except ApiError: pass
        for name in ['key','name','description']:
            vals[name]=QLineEdit((old or {}).get(name,'') if name!='key' else (key or '')); f.addRow(name,vals[name])
        role=QComboBox(); [role.addItem(x,x) for x in ['assistant','goals','main','planner','worker']]; f.addRow('role',role)
        ok=button('保存',primary=True); ok.clicked.connect(d.accept); f.addRow(ok)
        if d.exec()!=QDialog.Accepted:return
        body={k:v.text() for k,v in vals.items()}; body['role']=role.currentData()
        try:self.client.post('/api/agents' if not key else '/api/agents/'+key+'/config',json=body)
        except ApiError:pass
        self.refresh()

class SystemAgentDetailPage(SystemAgentsPage): pass