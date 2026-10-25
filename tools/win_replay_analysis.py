"""Small, user-operated front end for local serial replay analysis."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
LABELS = {'queued': '排队', 'running': '解析中', 'stopping': '正在停止',
          'completed': '完成', 'incomplete': '不完整', 'interrupted': '已中断', 'failed': '失败'}


class ReplayAnalysisWindow:
    def __init__(self, root):
        from dota2_insights.replay_workbench import ReplayQueue, dependency_status
        from dota2_insights.scrollable_panel import scrollable_body
        self.root, self.closing = root, False
        self.queue = ReplayQueue(ROOT)
        self.process = None
        root.title('本地录像分析')
        root.geometry('820x600')
        root.protocol('WM_DELETE_WINDOW', self.close)
        body, self.canvas = scrollable_body(root, padding=16, horizontal=True)
        ttk.Label(body, text='本地录像分析', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(body, text='选择已下载的DEM录像，逐场离线解析。Dota运行时等待；中途开游戏会停止当前解析。',
                  wraplength=740).pack(anchor='w', pady=8)
        controls = ttk.Frame(body)
        controls.pack(fill='x', pady=8)
        ttk.Button(controls, text='添加录像', command=self.add).pack(side='left')
        ttk.Button(controls, text='开始队列', command=self.start).pack(side='left', padx=8)
        ttk.Button(controls, text='停止', command=self.stop).pack(side='left')
        ttk.Button(controls, text='查看所选任务资料', command=self.open_result).pack(side='left', padx=8)
        self.table = ttk.Treeview(body, columns=('match', 'status', 'reason'), show='headings', height=12)
        for key, title, width in [('match', '比赛编号', 150), ('status', '状态', 90), ('reason', '说明', 480)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width)
        self.table.pack(fill='both', expand=True)
        self.queue.notice = dependency_status(ROOT) or self.queue.notice
        self.note = tk.StringVar(value=self.queue.notice)
        ttk.Label(body, textvariable=self.note, wraplength=740).pack(anchor='w', pady=12)
        ttk.Label(body, text='完成后保存英雄状态、现有事件时间线和来源指纹。未完成任务不自动重跑；原录像保持不变。',
                  wraplength=740).pack(anchor='w')
        self._poll_after = root.after(500, self.poll)

    def add(self):
        paths = filedialog.askopenfilenames(parent=self.root, title='选择以比赛编号命名的录像',
            initialdir=str(ROOT / 'diagnostics/replay-downloads'), filetypes=[('Dota录像', '*.dem')])
        errors = []
        for path in paths:
            try:
                self.queue.add(path)
            except (OSError, ValueError) as exc:
                errors.append(str(exc)[:200])
        if errors:
            messagebox.showerror('部分录像未加入', '\n'.join(errors[:5]), parent=self.root)
        self.refresh()

    def start(self):
        try:
            self.queue.start()
            self.note.set(self.queue.notice)
        except (OSError, ValueError) as exc:
            messagebox.showerror('暂时无法解析', str(exc)[:300], parent=self.root)

    def stop(self):
        try:
            self.queue.stop()
        except OSError as exc:
            self.note.set('停止请求写入失败：' + type(exc).__name__)
            if self.queue.process is not None:
                self.queue.process.kill()

    def refresh(self):
        for job in self.queue.jobs:
            values = (job['match_id'], LABELS.get(job['state'], '未知'), job.get('reason', ''))
            if self.table.exists(job['id']):
                self.table.item(job['id'], values=values)
            else:
                self.table.insert('', 'end', iid=job['id'], values=values)

    def open_result(self):
        chosen = self.table.selection()
        folder = self.queue.folder / chosen[0] if chosen else self.queue.folder
        if folder.is_dir():
            report = folder / 'fact-review' / 'report.html'
            os.startfile(str(report if report.is_file() else folder))
        else:
            self.note.set('这项任务尚未启动，没有生成资料目录。')

    def poll(self):
        self._poll_after = None
        try:
            self.queue.poll()
            self.process = self.queue.process
            self.refresh()
            self.note.set(self.queue.notice)
        except (OSError, ValueError) as exc:
            self.queue.enabled = False
            if self.queue.process is not None:
                self.queue.process.kill()
            self.note.set('任务状态异常，已停止排队：' + type(exc).__name__)
        if self.closing and self.queue.process is None:
            self.root.destroy()
            return
        self._poll_after = self.root.after(500, self.poll)

    def close(self):
        self.closing = True
        self.stop()
        if self.queue.process is None:
            if self._poll_after is not None:
                self.root.after_cancel(self._poll_after)
                self._poll_after = None
            self.root.destroy()
