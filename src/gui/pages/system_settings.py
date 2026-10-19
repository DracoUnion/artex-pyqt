from PySide6.QtWidgets import QFormLayout,QLineEdit,QSpinBox,QCheckBox,QPushButton
from ..widgets import button
from .base import BasePage
class SystemSettingsPage(BasePage):
 def title_text(self): return '系统配置'
 def _build(self):
  super()._build(); self.form=QFormLayout(); self.fields={}
  for k in ['global_proxy','python_interpreter','worker_concurrency']:
   w=QSpinBox() if k=='worker_concurrency' else QLineEdit(); self.fields[k]=w; self.form.addRow(k,w)
  for k in ['traffic_enabled','llm_record_enabled','web_search_enabled']:
   w=QCheckBox(); self.fields[k]=w; self.form.addRow(k,w)
  save=button('保存全部',primary=True); save.clicked.connect(self.save); self.content_lay.addLayout(self.form); self.content_lay.addWidget(save); self.refresh()
 def refresh(self):
  try:
   s=self.client.get('/api/settings')
   for k,w in self.fields.items():
    if k in s:
     w.setValue(int(s[k])) if hasattr(w,'setValue') and str(s[k]).isdigit() else (w.setChecked(str(s[k]).lower() in ('true','1')) if hasattr(w,'setChecked') else w.setText(str(s[k])))
  except Exception: pass
 def save(self):
  d={k:(w.value() if hasattr(w,'value') else w.isChecked() if hasattr(w,'isChecked') else w.text()) for k,w in self.fields.items()}; self.client.put('/api/settings',json=d)
