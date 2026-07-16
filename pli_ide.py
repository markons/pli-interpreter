"""A small stand-alone IDE for the pli interpreter (Tkinter, stdlib only).

    python pli_ide.py [program.pli]

Three functions:
  1. edit  - text editor with line numbers, open/save (Ctrl+O / Ctrl+S)
  2. compile (F7) - preprocess + parse only; errors appear in the
     "Errors" tab; double-click an error to jump to its line
  3. run (F5) - executes the program; SYSPRINT output appears in the
     "Output" tab.  GET ... first consumes any lines pre-typed in the
     "Input (SYSIN, pre-supplied)" tab; after that the SYSIN> console
     under the output pane activates and the program waits for you to
     type a line (Enter/Send).  A line of "/*" or the EOF button
     signals end-of-file (raises the ENDFILE condition).

The program runs on a worker thread so the window stays responsive.
"Stop" interrupts a program the next time it reads input or writes
output.
"""
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pli.lexer import LexError, reserved            # noqa: E402
from pli.parser import PLIParser, ParseError        # noqa: E402
from pli.preproc import preprocess, PreprocError    # noqa: E402
from pli.interpreter import (Interpreter, PLIError,           # noqa: E402
                             _BUILTINS, _NILADIC_BUILTINS)

KEYWORDS = set(reserved) | {"RECURSIVE", "MAIN", "SET", "INTO", "FROM",
                            "KEY", "KEYTO", "KEYFROM", "TITLE", "INPUT",
                            "OUTPUT", "UPDATE", "KEYED", "RECORD",
                            "STREAM", "PRINT", "ENV", "INDEXED", "EVENT",
                            "TASK", "PRIORITY", "POINTER", "PTR",
                            "POSITION", "COMPLEX"}
BUILTINS = set(_BUILTINS) | set(_NILADIC_BUILTINS)

# one scanner pass: comments and strings win over everything else
HL_RE = re.compile(
    r"(/\*.*?\*/)"                                    # 1 comment
    r"|('(?:[^']|'')*'[A-Za-z]?)"                     # 2 string (+B/I suffix)
    r"|([A-Za-z_$#@][A-Za-z0-9_$#@]*)"                # 3 name
    r"|((?:\d+\.\d*|\.\d+|\d+)(?:[Ee][+-]?\d+)?[A-Za-z]?)"  # 4 number
    r"|(%)",                                          # 5 preprocessor
    re.DOTALL)

HL_COLORS = {
    "hl_com": {"foreground": "#008000"},
    "hl_str": {"foreground": "#a31515"},
    "hl_kw": {"foreground": "#0000cc", "font": ("Consolas", 11, "bold")},
    "hl_bif": {"foreground": "#267f99"},
    "hl_num": {"foreground": "#098658"},
    "hl_pp": {"foreground": "#af00db",
              "font": ("Consolas", 11, "bold")},
}

FONT = ("Consolas", 11)
TEMPLATE = """HELLO: PROCEDURE OPTIONS(MAIN);
   DECLARE NAME CHAR(20) VARYING;
   PUT LIST('HELLO FROM THE PL/I IDE');
   GET LIST(NAME);
   PUT SKIP LIST('AND HELLO,', NAME);
END HELLO;
"""


class StopRun(Exception):
    pass


class GuiWriter:
    """stdout replacement: forwards writes to the GUI via a queue."""
    def __init__(self, q, stop_flag):
        self.q = q
        self.stop_flag = stop_flag

    def write(self, text):
        if self.stop_flag.is_set():
            raise StopRun()
        self.q.put(("out", text))

    def flush(self):
        pass


class GuiReader:
    """stdin replacement: serves pre-supplied SYSIN lines first, then
    asks the GUI for live input and blocks the worker thread until the
    user submits a line (or signals EOF with /* or the EOF button)."""
    def __init__(self, pretext, q, stop_flag):
        self.lines = pretext.splitlines(keepends=True)
        if self.lines and not self.lines[-1].endswith("\n"):
            self.lines[-1] += "\n"
        self.q = q
        self.stop_flag = stop_flag
        self.event = threading.Event()
        self.response = None
        self.eof = False

    def readline(self):
        if self.stop_flag.is_set():
            raise StopRun()
        if self.lines:
            return self.lines.pop(0)
        if self.eof:
            return ""
        self.event.clear()
        self.q.put(("input_req", None))
        while not self.event.wait(0.1):
            if self.stop_flag.is_set():
                raise StopRun()
        if self.response is None:            # EOF signalled
            self.eof = True
            return ""
        return self.response


class PLIIDE(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.geometry("1000x720")
        self.filename = None
        self.parser = PLIParser()          # built once, reused
        self.run_thread = None
        self.stop_flag = threading.Event()
        self.out_queue = queue.Queue()
        self._build_ui()
        self._bind_keys()
        if path and os.path.exists(path):
            self._load_file(path)
        else:
            self.editor.insert("1.0", TEMPLATE)
        self._retitle()
        self.after(50, self._poll_queue)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        # menu
        menu = tk.Menu(self)
        m_file = tk.Menu(menu, tearoff=0)
        m_file.add_command(label="New", command=self.new_file,
                           accelerator="Ctrl+N")
        m_file.add_command(label="Open...", command=self.open_file,
                           accelerator="Ctrl+O")
        m_file.add_command(label="Save", command=self.save_file,
                           accelerator="Ctrl+S")
        m_file.add_command(label="Save As...", command=self.save_file_as)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self.destroy)
        menu.add_cascade(label="File", menu=m_file)
        m_edit = tk.Menu(menu, tearoff=0)
        m_edit.add_command(label="Undo", accelerator="Ctrl+Z",
                           command=lambda: self._safe_edit("edit_undo"))
        m_edit.add_command(label="Redo", accelerator="Ctrl+Y",
                           command=lambda: self._safe_edit("edit_redo"))
        m_edit.add_separator()
        m_edit.add_command(label="Find / Replace...", accelerator="Ctrl+F",
                           command=self.show_find_dialog)
        m_edit.add_command(label="Find Next", accelerator="F3",
                           command=self.find_next)
        menu.add_cascade(label="Edit", menu=m_edit)
        m_run = tk.Menu(menu, tearoff=0)
        m_run.add_command(label="Compile", command=self.compile_program,
                          accelerator="F7")
        m_run.add_command(label="Run", command=self.run_program,
                          accelerator="F5")
        m_run.add_command(label="Stop", command=self.stop_program)
        menu.add_cascade(label="Run", menu=m_run)
        self.config(menu=menu)

        # toolbar
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Compile (F7)",
                   command=self.compile_program).pack(side="left", padx=2,
                                                      pady=2)
        ttk.Button(bar, text="Run (F5)",
                   command=self.run_program).pack(side="left", padx=2)
        ttk.Button(bar, text="Stop",
                   command=self.stop_program).pack(side="left", padx=2)

        # editor + bottom notebook in a vertical paned window
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True)

        edframe = ttk.Frame(paned)
        self.linenos = tk.Text(edframe, width=5, padx=4, takefocus=0,
                               state="disabled", font=FONT,
                               background="#f0f0f0", foreground="#606060",
                               borderwidth=0)
        self.linenos.pack(side="left", fill="y")
        self.editor = tk.Text(edframe, wrap="none", undo=True, font=FONT)
        yscroll = ttk.Scrollbar(edframe, orient="vertical",
                                command=self._yview_both)
        self.editor.configure(yscrollcommand=self._on_editor_scroll)
        yscroll.pack(side="right", fill="y")
        self.editor.pack(side="left", fill="both", expand=True)
        self.yscroll = yscroll
        for tag, cfg in HL_COLORS.items():
            self.editor.tag_configure(tag, **cfg)
        self.editor.tag_configure("errline", background="#ffd0d0")
        self.editor.tag_raise("errline")
        self.editor.tag_configure("search_hit", background="#ffe97a")
        self.editor.tag_raise("search_hit")
        self._hl_job = None
        self.find_dialog = None
        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.case_var = tk.BooleanVar(value=False)
        paned.add(edframe, weight=3)

        nb = ttk.Notebook(paned)
        self.errors = tk.Listbox(nb, font=FONT, foreground="#b00000")
        self.errors.bind("<Double-Button-1>", self._goto_error)
        nb.add(self.errors, text="Errors")

        # Output tab: SYSPRINT text + live SYSIN console row underneath
        outframe = ttk.Frame(nb)
        self.output = tk.Text(outframe, font=FONT, state="disabled",
                              background="#101010", foreground="#e0e0e0")
        self.output.tag_configure("stdin", foreground="#7ddc7d")
        self.output.pack(fill="both", expand=True)
        conrow = ttk.Frame(outframe)
        conrow.pack(fill="x")
        self.con_label = ttk.Label(conrow, text="SYSIN>")
        self.con_label.pack(side="left", padx=(4, 2))
        self.con_entry = ttk.Entry(conrow, font=FONT, state="disabled")
        self.con_entry.pack(side="left", fill="x", expand=True, padx=2,
                            pady=2)
        self.con_entry.bind("<Return>", lambda e: self._submit_input())
        self.con_send = ttk.Button(conrow, text="Send", state="disabled",
                                   command=self._submit_input)
        self.con_send.pack(side="left", padx=2)
        self.con_eof = ttk.Button(conrow, text="EOF", state="disabled",
                                  command=lambda:
                                  self._submit_input(eof=True))
        self.con_eof.pack(side="left", padx=(2, 4))
        nb.add(outframe, text="Output (SYSPRINT)")
        self.outframe = outframe

        self.sysin = tk.Text(nb, font=FONT, height=6)
        nb.add(self.sysin, text="Input (SYSIN, pre-supplied)")
        self.notebook = nb
        self.reader = None
        self._waiting_input = False
        paned.add(nb, weight=1)

        self.status = ttk.Label(self, text="ready", anchor="w")
        self.status.pack(fill="x")

        self.editor.bind("<<Modified>>", self._on_modified)
        self.editor.bind("<KeyRelease>", self._update_status)
        self.editor.bind("<ButtonRelease>", self._update_status)

    def _bind_keys(self):
        self.bind("<F7>", lambda e: self.compile_program())
        self.bind("<F5>", lambda e: self.run_program())
        self.bind("<F3>", lambda e: self.find_next())
        self.bind("<Control-n>", lambda e: self.new_file())
        self.bind("<Control-o>", lambda e: self.open_file())
        self.bind("<Control-s>", lambda e: self.save_file())
        self.bind("<Control-f>", lambda e: self.show_find_dialog())

    def _safe_edit(self, op):
        try:
            getattr(self.editor, op)()
        except tk.TclError:
            pass

    # ---- line numbers / status ---------------------------------------------

    def _yview_both(self, *args):
        self.editor.yview(*args)
        self.linenos.yview(*args)

    def _on_editor_scroll(self, first, last):
        self.yscroll.set(first, last)
        self.linenos.yview_moveto(first)

    def _on_modified(self, event=None):
        self.editor.edit_modified(False)
        lines = int(self.editor.index("end-1c").split(".")[0])
        text = "\n".join(str(i) for i in range(1, lines + 1))
        self.linenos.configure(state="normal")
        self.linenos.delete("1.0", "end")
        self.linenos.insert("1.0", text)
        self.linenos.configure(state="disabled")
        self.linenos.yview_moveto(self.editor.yview()[0])
        self._schedule_highlight()

    # ---- syntax highlighting -----------------------------------------------

    def _schedule_highlight(self):
        if self._hl_job is not None:
            self.after_cancel(self._hl_job)
        self._hl_job = self.after(120, self._highlight)

    def _highlight(self):
        self._hl_job = None
        text = self.editor.get("1.0", "end-1c")
        for tag in HL_COLORS:
            self.editor.tag_remove(tag, "1.0", "end")
        # byte-offset -> Tk line.col conversion table
        starts = [0]
        for ln in text.split("\n"):
            starts.append(starts[-1] + len(ln) + 1)

        import bisect

        def tk_index(off):
            line = bisect.bisect_right(starts, off)
            return "%d.%d" % (line, off - starts[line - 1])

        for m in HL_RE.finditer(text):
            if m.group(1):
                tag = "hl_com"
            elif m.group(2):
                tag = "hl_str"
            elif m.group(3):
                word = m.group(3).upper()
                if word in KEYWORDS:
                    tag = "hl_kw"
                elif word in BUILTINS:
                    tag = "hl_bif"
                else:
                    continue
            elif m.group(4):
                tag = "hl_num"
            else:
                tag = "hl_pp"
            self.editor.tag_add(tag, tk_index(m.start()), tk_index(m.end()))

    def _update_status(self, event=None):
        line, col = self.editor.index("insert").split(".")
        base = os.path.basename(self.filename) if self.filename \
            else "(untitled)"
        self.status.config(text="%s   line %s, col %d"
                           % (base, line, int(col) + 1))

    def _retitle(self):
        name = self.filename or "(untitled)"
        self.title("PL/I IDE - %s" % name)
        self._on_modified()
        self._update_status()

    # ---- file handling -------------------------------------------------------

    def new_file(self):
        self.editor.delete("1.0", "end")
        self.filename = None
        self._retitle()

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("PL/I programs", "*.pli *.pl1"),
                       ("All files", "*.*")])
        if path:
            self._load_file(path)

    def _load_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.filename = path
        self._retitle()

    def save_file(self):
        if not self.filename:
            return self.save_file_as()
        with open(self.filename, "w", encoding="utf-8") as f:
            f.write(self.editor.get("1.0", "end-1c"))
        self.status.config(text="saved %s" % self.filename)

    def save_file_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".pli",
            filetypes=[("PL/I programs", "*.pli *.pl1"),
                       ("All files", "*.*")])
        if path:
            self.filename = path
            self._retitle()
            self.save_file()

    # ---- find / replace -----------------------------------------------------

    def show_find_dialog(self):
        if self.find_dialog is not None and self.find_dialog.winfo_exists():
            self.find_dialog.lift()
            self.find_dialog.focus_set()
            return
        dlg = tk.Toplevel(self)
        dlg.title("Find / Replace")
        dlg.transient(self)
        dlg.resizable(False, False)
        self.find_dialog = dlg
        # pre-fill with the current selection, if any
        try:
            self.find_var.set(self.editor.get("sel.first", "sel.last"))
        except tk.TclError:
            pass
        ttk.Label(dlg, text="Find:").grid(row=0, column=0, sticky="e",
                                          padx=4, pady=4)
        e_find = ttk.Entry(dlg, textvariable=self.find_var, width=32,
                           font=FONT)
        e_find.grid(row=0, column=1, columnspan=3, padx=4, pady=4)
        ttk.Label(dlg, text="Replace:").grid(row=1, column=0, sticky="e",
                                             padx=4)
        ttk.Entry(dlg, textvariable=self.replace_var, width=32,
                  font=FONT).grid(row=1, column=1, columnspan=3, padx=4)
        ttk.Checkbutton(dlg, text="Match case",
                        variable=self.case_var).grid(row=2, column=1,
                                                     sticky="w", padx=4)
        ttk.Button(dlg, text="Find Next",
                   command=self.find_next).grid(row=3, column=1, padx=2,
                                                pady=6, sticky="ew")
        ttk.Button(dlg, text="Replace",
                   command=self.replace_one).grid(row=3, column=2, padx=2,
                                                  pady=6, sticky="ew")
        ttk.Button(dlg, text="Replace All",
                   command=self.replace_all).grid(row=3, column=3, padx=2,
                                                  pady=6, sticky="ew")
        e_find.bind("<Return>", lambda e: self.find_next())
        dlg.bind("<Escape>", lambda e: self._close_find())
        dlg.protocol("WM_DELETE_WINDOW", self._close_find)
        e_find.focus_set()
        e_find.select_range(0, "end")

    def _close_find(self):
        self.editor.tag_remove("search_hit", "1.0", "end")
        if self.find_dialog is not None:
            self.find_dialog.destroy()
            self.find_dialog = None
        self.editor.focus_set()

    def _mark_all_hits(self, pattern):
        self.editor.tag_remove("search_hit", "1.0", "end")
        if not pattern:
            return 0
        count = 0
        pos = "1.0"
        while True:
            pos = self.editor.search(pattern, pos, stopindex="end",
                                     nocase=not self.case_var.get())
            if not pos:
                break
            end = "%s+%dc" % (pos, len(pattern))
            self.editor.tag_add("search_hit", pos, end)
            pos = end
            count += 1
        return count

    def find_next(self):
        pattern = self.find_var.get()
        if not pattern:
            self.show_find_dialog()
            return False
        total = self._mark_all_hits(pattern)
        pos = self.editor.search(pattern, "insert", stopindex="end",
                                 nocase=not self.case_var.get())
        if not pos:                                       # wrap around
            pos = self.editor.search(pattern, "1.0", stopindex="end",
                                     nocase=not self.case_var.get())
        if not pos:
            self.status.config(text="'%s' not found" % pattern)
            return False
        end = "%s+%dc" % (pos, len(pattern))
        self.editor.tag_remove("sel", "1.0", "end")
        self.editor.tag_add("sel", pos, end)
        self.editor.mark_set("insert", end)
        self.editor.see(pos)
        self.editor.focus_set()
        self.status.config(text="%d match(es) for '%s'" % (total, pattern))
        return True

    def replace_one(self):
        pattern = self.find_var.get()
        if not pattern:
            return
        try:
            sel = self.editor.get("sel.first", "sel.last")
        except tk.TclError:
            sel = None
        matches = (sel == pattern if self.case_var.get()
                   else (sel or "").upper() == pattern.upper())
        if sel is not None and matches:
            self.editor.delete("sel.first", "sel.last")
            self.editor.insert("insert", self.replace_var.get())
        self.find_next()

    def replace_all(self):
        pattern = self.find_var.get()
        if not pattern:
            return
        repl = self.replace_var.get()
        count = 0
        pos = "1.0"
        while True:
            pos = self.editor.search(pattern, pos, stopindex="end",
                                     nocase=not self.case_var.get())
            if not pos:
                break
            self.editor.delete(pos, "%s+%dc" % (pos, len(pattern)))
            self.editor.insert(pos, repl)
            pos = "%s+%dc" % (pos, len(repl))
            count += 1
        self.editor.tag_remove("search_hit", "1.0", "end")
        self.status.config(text="replaced %d occurrence(s)" % count)

    # ---- compile ---------------------------------------------------------------

    def _clear_errors(self):
        self.errors.delete(0, "end")
        self.editor.tag_remove("errline", "1.0", "end")

    def _add_error(self, msg):
        self.errors.insert("end", msg)
        m = re.search(r"line (\d+)", msg)
        if m:
            line = m.group(1)
            self.editor.tag_add("errline", "%s.0" % line,
                                "%s.0 lineend" % line)

    def _goto_error(self, event=None):
        sel = self.errors.curselection()
        if not sel:
            return
        m = re.search(r"line (\d+)", self.errors.get(sel[0]))
        if m:
            self.editor.mark_set("insert", "%s.0" % m.group(1))
            self.editor.see("insert")
            self.editor.focus_set()

    def compile_program(self, quiet=False):
        """Preprocess + parse.  Returns True when the program is clean."""
        self._clear_errors()
        source = self.editor.get("1.0", "end-1c")
        incdir = os.path.dirname(self.filename) if self.filename else "."
        try:
            expanded = preprocess(source, incdir)
            self.parser.parse(expanded)
        except (LexError, ParseError, PreprocError, PLIError) as e:
            msgs = getattr(e, "messages", None) or [str(e)]
            for m in msgs:
                self._add_error("%s: %s" % (type(e).__name__, m))
            self.notebook.select(self.errors)
            self.status.config(text="compile failed - %d error(s)"
                               % len(msgs))
            return False
        if not quiet:
            self.status.config(text="compile OK - no errors")
        return True

    # ---- run -----------------------------------------------------------------

    def run_program(self):
        if self.run_thread and self.run_thread.is_alive():
            messagebox.showinfo("PL/I IDE", "A program is already running "
                                "- press Stop first.")
            return
        if not self.compile_program(quiet=True):
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self.notebook.select(self.outframe)
        self.status.config(text="running...")
        source = self.editor.get("1.0", "end-1c")
        incdir = os.path.dirname(self.filename) if self.filename else "."
        self.stop_flag.clear()
        sysin = GuiReader(self.sysin.get("1.0", "end-1c"),
                          self.out_queue, self.stop_flag)
        self.reader = sysin
        writer = GuiWriter(self.out_queue, self.stop_flag)

        def work():
            try:
                interp = Interpreter(stdin=sysin, stdout=writer)
                interp.parser = self.parser        # reuse built grammar
                interp.run(source, incdir)
                self.out_queue.put(("done", "program ended normally"))
            except StopRun:
                self.out_queue.put(("done", "stopped by user"))
            except (PLIError, ParseError, LexError, PreprocError) as e:
                self.out_queue.put(("err", "%s: %s"
                                    % (type(e).__name__, e)))
            except Exception as e:                 # interpreter bug guard
                self.out_queue.put(("err", "internal error: %r" % (e,)))

        self.run_thread = threading.Thread(target=work, daemon=True)
        self.run_thread.start()

    def stop_program(self):
        self.stop_flag.set()
        self._set_console(False)
        self.status.config(text="stop requested "
                           "(takes effect on next output or input)")

    # ---- live SYSIN console ------------------------------------------------

    def _set_console(self, waiting):
        self._waiting_input = waiting
        state = "normal" if waiting else "disabled"
        self.con_entry.configure(state=state)
        self.con_send.configure(state=state)
        self.con_eof.configure(state=state)
        if waiting:
            self.notebook.select(self.outframe)
            self.con_entry.focus_set()
            self.status.config(text="program is waiting for SYSIN input "
                               "(type a line, or /* / EOF button to end)")

    def _submit_input(self, eof=False):
        if not self._waiting_input or self.reader is None:
            return
        line = self.con_entry.get()
        if line.strip() == "/*":               # JCL in-stream delimiter
            eof = True
        self.con_entry.delete(0, "end")
        self._set_console(False)
        self.output.configure(state="normal")
        text = self.output.get("1.0", "end-1c")
        if text and not text.endswith("\n"):
            self.output.insert("end", "\n")
        self.output.insert("end",
                           "*EOF*\n" if eof else line + "\n", "stdin")
        self.output.see("end")
        self.output.configure(state="disabled")
        self.reader.response = None if eof else line + "\n"
        self.reader.event.set()
        self.status.config(text="running...")

    def _poll_queue(self):
        try:
            while True:
                kind, text = self.out_queue.get_nowait()
                if kind == "out":
                    self.output.configure(state="normal")
                    self.output.insert("end", text)
                    self.output.see("end")
                    self.output.configure(state="disabled")
                elif kind == "input_req":
                    self._set_console(True)
                elif kind == "done":
                    self._set_console(False)
                    self.status.config(text=text)
                elif kind == "err":
                    self._set_console(False)
                    self._add_error(text)
                    self.notebook.select(self.errors)
                    self.status.config(text="runtime error")
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    app = PLIIDE(args[0] if args else None)
    if "--selftest" in sys.argv:                  # open, then close by itself
        app.after(1500, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
