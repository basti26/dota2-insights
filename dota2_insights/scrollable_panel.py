"""Keep long tool forms reachable, including notebook pages and large fonts."""

import tkinter as tk
from tkinter import ttk


def scrollable_body(tab, *, padding=16, horizontal=False):
    canvas = tk.Canvas(tab, highlightthickness=0, borderwidth=0,
                       background=tab.winfo_toplevel().cget("background"))
    if horizontal:
        horizontal_bar = ttk.Scrollbar(tab, orient="horizontal", command=canvas.xview)
        horizontal_bar.pack(side="bottom", fill="x")
        canvas.configure(xscrollcommand=horizontal_bar.set)
    scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    body = ttk.Frame(canvas, padding=padding)
    item = canvas.create_window((0, 0), window=body, anchor="nw")
    def sync(_event=None):
        width = canvas.winfo_width()
        if width > 1:
            canvas.itemconfigure(item, width=max(width, body.winfo_reqwidth()) if horizontal else width)
        canvas.configure(scrollregion=canvas.bbox("all"))

    body.bind("<Configure>", sync)
    canvas.bind("<Configure>", sync)

    def wheel(event):
        try:
            if event.widget.winfo_toplevel() is not tab.winfo_toplevel() or not event.delta:
                return
            selected = getattr(tab.master, "select", None)
            if callable(selected) and selected() != str(tab):
                return
            direction = -1 if event.delta > 0 else 1
            scroll = canvas.xview_scroll if horizontal and event.state & 1 else canvas.yview_scroll
            scroll(direction * max(1, abs(int(event.delta / 120))), "units")
        except (AttributeError, tk.TclError):
            return

    tab.winfo_toplevel().bind("<MouseWheel>", wheel, add="+")

    def reveal_focus(event):
        widget = event.widget
        if not str(widget).startswith(str(body) + "."):
            return
        try:
            bounds = canvas.bbox("all")
            if not bounds:
                return
            x, y = widget.winfo_rootx() - body.winfo_rootx(), widget.winfo_rooty() - body.winfo_rooty()
            for start, size, view, viewport, move, extent in (
                (y, widget.winfo_height(), canvas.canvasy(0), canvas.winfo_height(), canvas.yview_moveto, bounds[3]),
                (x, widget.winfo_width(), canvas.canvasx(0), canvas.winfo_width(), canvas.xview_moveto, bounds[2]),
            ):
                if extent > viewport and (start < view or start + size > view + viewport):
                    move(max(0, start - 12) / extent)
        except tk.TclError:
            pass

    tab.winfo_toplevel().bind("<FocusIn>", reveal_focus, add="+")
    return body, canvas
