"""
VB6 → VB.NET Phase 1 v5.0 — SEMANTIC ANALYSIS ENGINE

Objective: Fully understand the source application. Build a complete semantic model.
Do NOT generate migrated code. Only produce analysis artifacts.

Output: Symbol table, event map, call graph, object flow, namespace map, risk report.
These become the sole source of truth for Phase 2.
"""

import json
import boto3
import logging
import zipfile
import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "vb6-input")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "vbnet-output")
BEDROCK_MODEL = os.environ.get("CLAUDE_MODEL_ID", "deepseek.v3.2")
MAX_TOKENS = int(os.environ.get("PHASE1_MAX_TOKENS", "16000"))
MAX_SOURCE_CHARS = int(os.environ.get("MAX_PHASE1_INPUT_CHARS", "120000"))

boto_cfg = Config(retries={"max_attempts": 5, "mode": "standard"}, read_timeout=300, connect_timeout=30)
s3 = boto3.client("s3", region_name=AWS_REGION, config=boto_cfg)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION, config=boto_cfg)

# VB6 → .NET control mapping (comprehensive)
CONTROL_MAP = {
    "VB.CommandButton": "System.Windows.Forms.Button",
    "VB.TextBox": "System.Windows.Forms.TextBox",
    "VB.Label": "System.Windows.Forms.Label",
    "VB.ListBox": "System.Windows.Forms.ListBox",
    "VB.ComboBox": "System.Windows.Forms.ComboBox",
    "VB.CheckBox": "System.Windows.Forms.CheckBox",
    "VB.OptionButton": "System.Windows.Forms.RadioButton",
    "VB.Frame": "System.Windows.Forms.GroupBox",
    "VB.PictureBox": "System.Windows.Forms.PictureBox",
    "VB.Timer": "System.Windows.Forms.Timer",
    "VB.ProgressBar": "System.Windows.Forms.ProgressBar",
    "VB.HScrollBar": "System.Windows.Forms.HScrollBar",
    "VB.VScrollBar": "System.Windows.Forms.VScrollBar",
    "VB.Image": "System.Windows.Forms.PictureBox",
    "VB.Data": "System.Windows.Forms.BindingSource",
    "VB.Shape": "System.Windows.Forms.Panel",
    "VB.Line": "System.Windows.Forms.Label",
    "VB.Menu": "System.Windows.Forms.ToolStripMenuItem",
    "VB.MDIForm": "System.Windows.Forms.Form",
    "VB.CommonDialog": "System.Windows.Forms.OpenFileDialog",
    "MSFlexGridLib.MSFlexGrid": "System.Windows.Forms.DataGridView",
    "MSComctlLib.TreeView": "System.Windows.Forms.TreeView",
    "MSComctlLib.ListView": "System.Windows.Forms.ListView",
    "MSComctlLib.TabStrip": "System.Windows.Forms.TabControl",
    "MSComctlLib.StatusBar": "System.Windows.Forms.StatusStrip",
    "MSComctlLib.Toolbar": "System.Windows.Forms.ToolStrip",
    "MSComctlLib.ProgressBar": "System.Windows.Forms.ProgressBar",
    "MSComctlLib.ImageList": "System.Windows.Forms.ImageList",
    "RichTextLib.RichTextBox": "System.Windows.Forms.RichTextBox",
    "MSComDlg.CommonDialog": "System.Windows.Forms.OpenFileDialog",
    "TabDlg.SSTab": "System.Windows.Forms.TabControl",
    "CommandButton": "System.Windows.Forms.Button",
    "TextBox": "System.Windows.Forms.TextBox",
    "Label": "System.Windows.Forms.Label",
    "ListBox": "System.Windows.Forms.ListBox",
    "ComboBox": "System.Windows.Forms.ComboBox",
    "CheckBox": "System.Windows.Forms.CheckBox",
    "Frame": "System.Windows.Forms.GroupBox",
    "PictureBox": "System.Windows.Forms.PictureBox",
    "Timer": "System.Windows.Forms.Timer",
    "MSFlexGrid": "System.Windows.Forms.DataGridView",
    "Menu": "System.Windows.Forms.ToolStripMenuItem",
    "StatusBar": "System.Windows.Forms.StatusStrip",
}


# ============================================================================
# DATA MODEL — Complete Semantic Representation
# ============================================================================

@dataclass
class SymbolEntry:
    name: str
    kind: str        # Method, Property, Field, Constant, Event, Variable
    owner: str       # Class/Module/Form that owns this
    scope: str       # Public, Private, Friend
    data_type: str = ""
    params: str = ""
    callers: List[str] = field(default_factory=list)
    callees: List[str] = field(default_factory=list)

@dataclass
class ControlEntry:
    name: str
    vb6_type: str
    dotnet_type: str
    caption: str = ""
    left: int = 0
    top: int = 0
    width: int = 100
    height: int = 25
    tab_index: int = 0
    visible: bool = True
    enabled: bool = True
    events: List[str] = field(default_factory=list)
    properties: Dict[str, str] = field(default_factory=dict)

@dataclass
class EventEntry:
    control_name: str
    event_name: str
    handler_name: str   # e.g. cmdSave_Click
    handler_body: str
    calls_methods: List[str] = field(default_factory=list)
    calls_forms: List[str] = field(default_factory=list)
    has_sql: bool = False
    has_validation: bool = False
    has_error_handling: bool = False
    has_file_io: bool = False

@dataclass
class MethodEntry:
    name: str
    owner: str
    scope: str
    kind: str           # Sub, Function
    params: str = ""
    return_type: str = ""
    body: str = ""
    calls: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)
    has_sql: bool = False
    has_error_handling: bool = False

@dataclass
class VariableEntry:
    name: str
    owner: str
    scope: str
    data_type: str
    initial_value: str = ""

@dataclass
class FormModel:
    name: str
    caption: str = ""
    form_type: str = "Standard"
    width: int = 500
    height: int = 400
    startup_position: str = "CenterScreen"
    controls: List[ControlEntry] = field(default_factory=list)
    events: List[EventEntry] = field(default_factory=list)
    methods: List[MethodEntry] = field(default_factory=list)
    variables: List[VariableEntry] = field(default_factory=list)
    menu_items: List[str] = field(default_factory=list)
    raw_header: str = ""
    raw_code: str = ""
    loc: int = 0

@dataclass
class ModuleModel:
    name: str
    kind: str  # Module, Class
    methods: List[MethodEntry] = field(default_factory=list)
    variables: List[VariableEntry] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    raw_code: str = ""
    loc: int = 0

@dataclass
class ProjectModel:
    name: str
    forms: Dict[str, FormModel] = field(default_factory=dict)
    modules: Dict[str, ModuleModel] = field(default_factory=dict)
    classes: Dict[str, ModuleModel] = field(default_factory=dict)
    references: List[str] = field(default_factory=list)
    startup_form: str = ""

    # Derived analysis
    symbol_table: List[SymbolEntry] = field(default_factory=list)
    call_graph: Dict[str, List[str]] = field(default_factory=dict)
    sql_queries: List[str] = field(default_factory=list)
    api_declares: List[str] = field(default_factory=list)
    file_operations: List[str] = field(default_factory=list)
    missing_apis: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    total_loc: int = 0
    total_controls: int = 0
    total_events: int = 0
    total_methods: int = 0


# ============================================================================
# VB6 PARSER — Deep Extraction
# ============================================================================

class VB6Parser:
    RE_CONTROL = re.compile(r'^\s*Begin\s+([\w.]+)\s+(\w+)', re.MULTILINE)
    RE_MENU = re.compile(r'^\s*Begin\s+VB\.Menu\s+(\w+)', re.MULTILINE)
    RE_EVENT = re.compile(r'^Private\s+Sub\s+(\w+)_(\w+)\s*\(([^)]*)\)', re.MULTILINE)
    RE_METHOD = re.compile(r'^(Public|Private|Friend)?\s*(Static\s+)?(Sub|Function)\s+(\w+)\s*\(([^)]*)\)(?:\s+As\s+(\w+))?', re.MULTILINE)
    RE_VAR = re.compile(r'^(Public|Private|Dim|Global)\s+(\w+)(?:\(.*?\))?\s+As\s+(\w+)', re.MULTILINE)
    RE_SQL = re.compile(r'(?:\"(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|EXEC)\s[^\"]+\")', re.IGNORECASE)
    RE_API = re.compile(r'^\s*(Public|Private)?\s*Declare\s+(Sub|Function)\s+(\w+)\s+Lib\s+\"([^\"]+)\"', re.MULTILINE)
    RE_ON_ERROR = re.compile(r'On\s+Error\s+(Resume\s+Next|GoTo\s+\w+)', re.IGNORECASE)
    RE_FILE_OP = re.compile(r'\b(Open|Close|Print\s*#|Write\s*#|Input\s*#|Kill|FileCopy|MkDir|RmDir)\b', re.IGNORECASE)
    RE_FORM_NAV = re.compile(r'(\w+)\.(Show|Hide|Unload|Load)\b', re.IGNORECASE)
    RE_CALL = re.compile(r'\bCall\s+(\w+)', re.IGNORECASE)
    RE_REF = re.compile(r'^Reference\s*=\s*(.+)$', re.MULTILINE)
    RE_STARTUP = re.compile(r'^Startup\s*=\s*"?(\w+)"?', re.MULTILINE)

    SKIP_EVENTS = {"Click", "DblClick", "Load", "Change", "KeyPress", "KeyDown", "KeyUp",
                   "GotFocus", "LostFocus", "Validate", "Initialize", "Terminate", "Resize",
                   "Activate", "Deactivate", "QueryUnload", "Unload", "MouseMove", "MouseDown",
                   "MouseUp", "Timer", "Scroll", "ItemClick", "ColumnClick"}

    def __init__(self):
        self.log = logging.getLogger(__name__)

    def extract_zip(self, path: str) -> Dict[str, Dict[str, str]]:
        files = {"forms": {}, "modules": {}, "classes": {}, "project": ""}
        with zipfile.ZipFile(path, "r") as z:
            for info in z.filelist:
                if info.is_dir():
                    continue
                ext = Path(info.filename).suffix.lower()
                stem = Path(info.filename).stem
                raw = z.read(info.filename).decode("utf-8", errors="replace")
                if ext == ".frm":
                    files["forms"][stem] = raw
                elif ext == ".bas":
                    files["modules"][stem] = raw
                elif ext == ".cls":
                    files["classes"][stem] = raw
                elif ext == ".vbp":
                    files["project"] = raw
        self.log.info(f"Extracted: {len(files['forms'])} forms, {len(files['modules'])} modules, {len(files['classes'])} classes")
        return files

    def parse_form(self, name: str, raw: str) -> FormModel:
        # Split header from code
        code_start = len(raw)
        for marker in ["Attribute VB_Name", "Option Explicit", "Private Sub", "Public Sub", "Dim ", "Private Function"]:
            idx = raw.find(marker)
            if idx != -1 and idx < code_start:
                code_start = idx
        header = raw[:code_start]
        code = raw[code_start:]

        form = FormModel(
            name=name,
            caption=self._prop(header, "Caption", name),
            form_type=self._form_type(header, code),
            width=self._prop_int(header, "ClientWidth", 500),
            height=self._prop_int(header, "ClientHeight", 400),
            raw_header=header,
            raw_code=code,
            loc=len([l for l in code.splitlines() if l.strip()]),
        )

        form.controls = self._parse_controls(header, name)
        form.events = self._parse_events(code, form.controls)
        form.methods = self._parse_methods(code, name, exclude_events=True)
        form.variables = self._parse_variables(code, name)
        form.menu_items = self.RE_MENU.findall(header)

        # Wire events to controls
        for evt in form.events:
            for ctrl in form.controls:
                if ctrl.name == evt.control_name and evt.event_name not in ctrl.events:
                    ctrl.events.append(evt.event_name)

        return form

    def _parse_controls(self, header: str, form_name: str) -> List[ControlEntry]:
        controls = []
        lines = header.splitlines()
        i = 0
        while i < len(lines):
            m = self.RE_CONTROL.match(lines[i])
            if m:
                vb6_type = m.group(1)
                ctrl_name = m.group(2)
                if vb6_type in ("VB.Form", "VB.MDIForm"):
                    i += 1
                    continue
                dotnet = CONTROL_MAP.get(vb6_type, f"UNMAPPED:{vb6_type}")
                props = {}
                i += 1
                depth = 1
                while i < len(lines) and depth > 0:
                    line = lines[i].strip()
                    if line.startswith("Begin "):
                        depth += 1
                    elif line == "End":
                        depth -= 1
                    elif "=" in line and depth == 1:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip().strip('"')
                    i += 1
                controls.append(ControlEntry(
                    name=ctrl_name, vb6_type=vb6_type, dotnet_type=dotnet,
                    caption=props.get("Caption", ""),
                    left=int(props.get("Left", "0")),
                    top=int(props.get("Top", "0")),
                    width=int(props.get("Width", "100")),
                    height=int(props.get("Height", "25")),
                    tab_index=int(props.get("TabIndex", "0")),
                    visible=props.get("Visible", "-1") != "0",
                    enabled=props.get("Enabled", "-1") != "0",
                    properties=props,
                ))
            else:
                i += 1
        return controls

    def _parse_events(self, code: str, controls: List[ControlEntry]) -> List[EventEntry]:
        events = []
        ctrl_names = {c.name for c in controls}
        ctrl_names.add("Form")
        for m in self.RE_EVENT.finditer(code):
            ctrl, evt = m.group(1), m.group(2)
            body = self._extract_body(code, m.start())
            events.append(EventEntry(
                control_name=ctrl, event_name=evt,
                handler_name=f"{ctrl}_{evt}",
                handler_body=body,
                calls_methods=self._find_calls(body),
                calls_forms=[x[0] for x in self.RE_FORM_NAV.findall(body)],
                has_sql=bool(self.RE_SQL.search(body)),
                has_validation=bool(re.search(r'\bLen\b|\bIsNumeric\b|\bTrim\b|\bVal\b|\.Text\s*=\s*""', body, re.IGNORECASE)),
                has_error_handling=bool(self.RE_ON_ERROR.search(body)),
                has_file_io=bool(self.RE_FILE_OP.search(body)),
            ))
        return events

    def _parse_methods(self, code: str, owner: str, exclude_events: bool = False) -> List[MethodEntry]:
        methods = []
        for m in self.RE_METHOD.finditer(code):
            scope = m.group(1) or "Private"
            kind = m.group(3)
            name = m.group(4)
            params = m.group(5)
            ret = m.group(6) or ""
            if exclude_events and "_" in name:
                suffix = name.split("_", 1)[1]
                if suffix in self.SKIP_EVENTS:
                    continue
            body = self._extract_body(code, m.start())
            methods.append(MethodEntry(
                name=name, owner=owner, scope=scope, kind=kind,
                params=params, return_type=ret, body=body,
                calls=self._find_calls(body),
                has_sql=bool(self.RE_SQL.search(body)),
                has_error_handling=bool(self.RE_ON_ERROR.search(body)),
            ))
        return methods

    def _parse_variables(self, code: str, owner: str) -> List[VariableEntry]:
        return [VariableEntry(name=m[1], owner=owner, scope=m[0], data_type=m[2])
                for m in self.RE_VAR.findall(code)]

    def parse_module(self, name: str, raw: str, kind: str) -> ModuleModel:
        mod = ModuleModel(name=name, kind=kind, raw_code=raw,
                          loc=len([l for l in raw.splitlines() if l.strip()]))
        mod.methods = self._parse_methods(raw, name)
        mod.variables = self._parse_variables(raw, name)
        # Extract properties for classes
        if kind == "Class":
            mod.properties = re.findall(r'Property\s+(?:Get|Let|Set)\s+(\w+)', raw)
        return mod

    def parse_project(self, vbp: str) -> Tuple[List[str], str]:
        refs = self.RE_REF.findall(vbp)
        startup_match = self.RE_STARTUP.search(vbp)
        startup = startup_match.group(1) if startup_match else ""
        return refs, startup

    def _extract_body(self, code: str, start: int) -> str:
        for end_marker in ["\nEnd Sub", "\nEnd Function"]:
            end = code.find(end_marker, start)
            if end != -1:
                return code[start:end + len(end_marker)]
        return code[start:start + 500]

    def _find_calls(self, body: str) -> List[str]:
        calls = set(self.RE_CALL.findall(body))
        kw = {"If", "For", "While", "Do", "Select", "Case", "Sub", "Function",
              "Dim", "Set", "Let", "End", "Exit", "With", "Print", "Debug", "Err", "MsgBox"}
        paren = re.findall(r'\b(\w+)\s*\(', body)
        calls.update(c for c in paren if c not in kw and not c.startswith("VB"))
        # Also find dot calls: Object.Method
        dot_calls = re.findall(r'(\w+)\.(\w+)\s*(?:\(|$)', body, re.MULTILINE)
        calls.update(f"{o}.{m}" for o, m in dot_calls if o not in kw)
        return list(calls)

    def _form_type(self, header: str, code: str) -> str:
        if "MDIChild" in header:
            return "MDI Child"
        if "vbModal" in code or "Show 1" in code:
            return "Modal Dialog"
        return "Standard"

    def _prop(self, text: str, name: str, default: str = "") -> str:
        m = re.search(rf'{name}\s*=\s*"?([^"\r\n]+)', text, re.IGNORECASE)
        return m.group(1).strip().strip('"') if m else default

    def _prop_int(self, text: str, name: str, default: int = 0) -> int:
        m = re.search(rf'{name}\s*=\s*(\d+)', text, re.IGNORECASE)
        return int(m.group(1)) if m else default


# ============================================================================
# SEMANTIC ANALYZER — Build Complete Project Model
# ============================================================================

class SemanticAnalyzer:
    def __init__(self, parser: VB6Parser):
        self.parser = parser
        self.log = logging.getLogger(__name__)

    def analyze(self, files: Dict, project_name: str) -> ProjectModel:
        model = ProjectModel(name=project_name)

        # Parse forms
        for name, raw in files["forms"].items():
            form = self.parser.parse_form(name, raw)
            model.forms[name] = form
            model.total_loc += form.loc
            model.total_controls += len(form.controls)
            model.total_events += len(form.events)
            model.total_methods += len(form.methods)

        # Parse modules
        for name, raw in files["modules"].items():
            mod = self.parser.parse_module(name, raw, "Module")
            model.modules[name] = mod
            model.total_loc += mod.loc
            model.total_methods += len(mod.methods)

        # Parse classes
        for name, raw in files["classes"].items():
            cls = self.parser.parse_module(name, raw, "Class")
            model.classes[name] = cls
            model.total_loc += cls.loc
            model.total_methods += len(cls.methods)

        # Parse project file
        if files["project"]:
            refs, startup = self.parser.parse_project(files["project"])
            model.references = refs
            model.startup_form = startup

        # Detect startup form if not in .vbp
        if not model.startup_form and model.forms:
            for fname in model.forms:
                if fname.lower().startswith("frmmain") or fname.lower() == "frmmain":
                    model.startup_form = fname
                    break
            if not model.startup_form:
                model.startup_form = list(model.forms.keys())[0]

        # Build symbol table
        self._build_symbol_table(model)

        # Build call graph
        self._build_call_graph(model)

        # Extract project-wide patterns
        all_code = "\n".join(
            [f.raw_code for f in model.forms.values()] +
            [m.raw_code for m in model.modules.values()] +
            [c.raw_code for c in model.classes.values()]
        )
        model.sql_queries = [q.strip('"') for q in self.parser.RE_SQL.findall(all_code)]
        model.api_declares = [f"{m[2]} (Lib: {m[3]})" for m in self.parser.RE_API.findall(all_code)]
        model.file_operations = list(set(self.parser.RE_FILE_OP.findall(all_code)))

        # Identify risks
        self._identify_risks(model)

        self.log.info(f"Analysis complete: {model.total_loc} LOC, {model.total_controls} controls, "
                     f"{model.total_events} events, {model.total_methods} methods, "
                     f"{len(model.sql_queries)} SQL, {len(model.api_declares)} APIs, "
                     f"{len(model.risks)} risks")

        return model

    def _build_symbol_table(self, model: ProjectModel):
        for fname, form in model.forms.items():
            for m in form.methods:
                model.symbol_table.append(SymbolEntry(
                    name=m.name, kind="Method", owner=fname, scope=m.scope,
                    data_type=m.return_type, params=m.params
                ))
            for v in form.variables:
                model.symbol_table.append(SymbolEntry(
                    name=v.name, kind="Variable", owner=fname, scope=v.scope, data_type=v.data_type
                ))
            for c in form.controls:
                model.symbol_table.append(SymbolEntry(
                    name=c.name, kind="Control", owner=fname, scope="Friend", data_type=c.dotnet_type
                ))
        for mname, mod in {**model.modules, **model.classes}.items():
            for m in mod.methods:
                model.symbol_table.append(SymbolEntry(
                    name=m.name, kind="Method", owner=mname, scope=m.scope,
                    data_type=m.return_type, params=m.params
                ))

    def _build_call_graph(self, model: ProjectModel):
        for fname, form in model.forms.items():
            for evt in form.events:
                model.call_graph[f"{fname}.{evt.handler_name}"] = evt.calls_methods
            for m in form.methods:
                model.call_graph[f"{fname}.{m.name}"] = m.calls
        for mname, mod in {**model.modules, **model.classes}.items():
            for m in mod.methods:
                model.call_graph[f"{mname}.{m.name}"] = m.calls

    def _identify_risks(self, model: ProjectModel):
        # Unmapped controls
        for form in model.forms.values():
            for c in form.controls:
                if c.dotnet_type.startswith("UNMAPPED"):
                    model.risks.append(f"Unmapped control: {form.name}.{c.name} ({c.vb6_type})")
        # API declares
        if model.api_declares:
            model.risks.append(f"{len(model.api_declares)} Windows API declares require P/Invoke")
        # COM references
        com_refs = [r for r in model.references if "GUID" in r or "{" in r]
        if com_refs:
            model.risks.append(f"{len(com_refs)} COM/ActiveX references may need replacement")


# ============================================================================
# BEDROCK LLM — For Enhanced Analysis
# ============================================================================

class LLM:
    def __init__(self):
        self.log = logging.getLogger(__name__)

    def call(self, prompt: str, max_tokens: int = MAX_TOKENS) -> str:
        try:
            self.log.info(f"LLM call: {len(prompt)} chars")
            resp = bedrock.invoke_model(
                modelId=BEDROCK_MODEL,
                body=json.dumps({"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}),
            )
            body = json.loads(resp["body"].read())
            if "content" in body:
                c = body["content"]
                return c[0].get("text", "") if isinstance(c, list) and c else (c if isinstance(c, str) else "")
            if "choices" in body and body["choices"]:
                return body["choices"][0].get("message", {}).get("content", "")
            return ""
        except Exception as e:
            self.log.error(f"LLM error: {e}", exc_info=True)
            return ""


# ============================================================================
# SPEC DOCUMENT GENERATOR — All AI-Powered, All Project-Specific
# ============================================================================

class SpecGenerator:
    def __init__(self, llm: LLM, model: ProjectModel):
        self.llm = llm
        self.model = model

    def generate_all(self) -> Dict[str, str]:
        source = self._source_chunk()
        summary = self._summary()

        docs = {}

        # 1. SPEC — comprehensive, with full symbol table embedded
        logger.info("Generating spec.md...")
        docs[f"{self.model.name}_spec.md"] = self._gen_spec(summary, source)

        # 2. SYMBOL TABLE — machine-readable JSON for Phase 2
        logger.info("Generating symbol_table.json...")
        docs[f"{self.model.name}_symbol_table.json"] = self._gen_symbol_table_json()

        # 3. EVENT MAP
        logger.info("Generating event_map.md...")
        docs[f"{self.model.name}_event_map.md"] = self._gen_event_map(summary)

        # 4. CALL GRAPH
        logger.info("Generating call_graph.md...")
        docs[f"{self.model.name}_call_graph.md"] = self._gen_call_graph(summary)

        # 5. BUSINESS RULES
        logger.info("Generating business_rules.md...")
        docs[f"{self.model.name}_business_rules.md"] = self._gen_business_rules(summary, source)

        # 6. UI NAVIGATION
        logger.info("Generating ui_navigation.md...")
        docs[f"{self.model.name}_ui_navigation.md"] = self._gen_navigation(summary)

        # 7. RISK REPORT
        logger.info("Generating risk_report.md...")
        docs[f"{self.model.name}_risk_report.md"] = self._gen_risk_report(summary)

        # 8. MIGRATION PLAN
        logger.info("Generating migration_plan.md...")
        docs[f"{self.model.name}_migration_plan.md"] = self._gen_migration_plan(summary)

        return docs

    def _summary(self) -> str:
        m = self.model
        ctrl_lines = []
        evt_lines = []
        method_lines = []
        for fname, form in m.forms.items():
            for c in form.controls:
                ctrl_lines.append(f"  {fname}.{c.name}: {c.vb6_type} → {c.dotnet_type} (Caption: {c.caption})")
            for e in form.events:
                evt_lines.append(f"  {fname}.{e.handler_name} → calls: {', '.join(e.calls_methods[:5])}")
            for mt in form.methods:
                method_lines.append(f"  {fname}.{mt.name}({mt.params}) As {mt.return_type or 'Sub'} [{mt.scope}]")
        for mname, mod in {**m.modules, **m.classes}.items():
            for mt in mod.methods:
                method_lines.append(f"  {mname}.{mt.name}({mt.params}) As {mt.return_type or 'Sub'} [{mt.scope}]")

        return f"""PROJECT: {m.name}
STARTUP FORM: {m.startup_form}
TOTAL LOC: {m.total_loc}
FORMS: {len(m.forms)} — {', '.join(m.forms.keys())}
MODULES: {len(m.modules)} — {', '.join(m.modules.keys())}
CLASSES: {len(m.classes)} — {', '.join(m.classes.keys())}
CONTROLS: {m.total_controls}
EVENTS: {m.total_events}
METHODS: {m.total_methods}
SQL QUERIES: {len(m.sql_queries)}
API DECLARES: {len(m.api_declares)}
FILE OPERATIONS: {', '.join(m.file_operations) if m.file_operations else 'None'}
RISKS: {len(m.risks)}

ALL CONTROLS:
{chr(10).join(ctrl_lines[:60])}

ALL EVENTS:
{chr(10).join(evt_lines[:40])}

ALL METHODS:
{chr(10).join(method_lines[:40])}

CALL GRAPH:
{chr(10).join(f'  {k} → {", ".join(v[:5])}' for k, v in list(m.call_graph.items())[:30])}

RISKS:
{chr(10).join(f'  - {r}' for r in m.risks)}
"""

    def _source_chunk(self) -> str:
        chunks = []
        for fname, form in self.model.forms.items():
            chunks.append(f"=== FORM: {fname} ({form.loc} LOC) ===\n{form.raw_code[:3000]}")
        for mname, mod in self.model.modules.items():
            chunks.append(f"=== MODULE: {mname} ({mod.loc} LOC) ===\n{mod.raw_code[:2000]}")
        for cname, cls in self.model.classes.items():
            chunks.append(f"=== CLASS: {cname} ({cls.loc} LOC) ===\n{cls.raw_code[:2000]}")
        return "\n\n".join(chunks)[:MAX_SOURCE_CHARS]

    def _gen_spec(self, summary: str, source: str) -> str:
        prompt = f"""You are a VB6 reverse engineering expert. Generate a comprehensive specification.

{summary}

ACTUAL SOURCE CODE:
{source}

Generate markdown with these sections:
1. Executive Summary (startup form: {self.model.startup_form})
2. Application Overview (all statistics must match exactly)
3. Forms & Screens (each form: purpose, ALL controls with VB6→.NET mapping, layout)
4. Control Inventory Table (Name | VB6 Type | .NET Type | Caption | Events)
5. Event Handlers (each: what it does, methods called, validation, SQL)
6. Methods & Functions (each: signature, purpose, calls, returns)
7. Database Interactions
8. Business Rules & Validation
9. Form Navigation & Workflows
10. External Dependencies
11. Error Handling Patterns
12. VB6 → .NET Control Mapping
13. Conversion Readiness Assessment
14. Per-Form Confidence Levels

Use ONLY actual data. Do NOT invent anything."""
        return self.llm.call(prompt) or f"# Spec generation failed\n\n{summary}"

    def _gen_symbol_table_json(self) -> str:
        """Machine-readable symbol table for Phase 2."""
        m = self.model
        data = {
            "project": m.name,
            "startup_form": m.startup_form,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "forms": {},
            "modules": {},
            "classes": {},
            "call_graph": m.call_graph,
            "risks": m.risks,
        }
        for fname, form in m.forms.items():
            data["forms"][fname] = {
                "caption": form.caption,
                "type": form.form_type,
                "width_twips": form.width,
                "height_twips": form.height,
                "controls": [{
                    "name": c.name, "vb6_type": c.vb6_type, "dotnet_type": c.dotnet_type,
                    "caption": c.caption, "events": c.events,
                    "left": c.left, "top": c.top, "width": c.width, "height": c.height,
                    "tab_index": c.tab_index, "visible": c.visible,
                } for c in form.controls],
                "events": [{
                    "handler": e.handler_name, "control": e.control_name,
                    "event": e.event_name, "calls": e.calls_methods,
                    "has_sql": e.has_sql, "has_validation": e.has_validation,
                    "body": e.handler_body[:500],
                } for e in form.events],
                "methods": [{
                    "name": mt.name, "scope": mt.scope, "kind": mt.kind,
                    "params": mt.params, "return_type": mt.return_type,
                    "calls": mt.calls, "body": mt.body[:500],
                } for mt in form.methods],
                "menus": form.menu_items,
                "variables": [{"name": v.name, "type": v.data_type, "scope": v.scope} for v in form.variables],
            }
        for mname, mod in m.modules.items():
            data["modules"][mname] = {
                "methods": [{"name": mt.name, "scope": mt.scope, "kind": mt.kind,
                            "params": mt.params, "return_type": mt.return_type,
                            "calls": mt.calls, "body": mt.body[:500]} for mt in mod.methods],
                "variables": [{"name": v.name, "type": v.data_type, "scope": v.scope} for v in mod.variables],
            }
        for cname, cls in m.classes.items():
            data["classes"][cname] = {
                "methods": [{"name": mt.name, "scope": mt.scope, "kind": mt.kind,
                            "params": mt.params, "return_type": mt.return_type,
                            "calls": mt.calls, "body": mt.body[:500]} for mt in cls.methods],
                "properties": cls.properties,
                "variables": [{"name": v.name, "type": v.data_type, "scope": v.scope} for v in cls.variables],
            }
        return json.dumps(data, indent=2)

    def _gen_event_map(self, summary: str) -> str:
        prompt = f"""Generate a complete Event Map for this VB6 application.

{summary}

For EACH event handler, document:
- Control → Event → Handler → What it does → Methods called → Forms navigated → Validation → Error handling

Use exact control names. Use exact method names. Do NOT invent anything."""
        return self.llm.call(prompt) or "# Event map generation failed"

    def _gen_call_graph(self, summary: str) -> str:
        prompt = f"""Generate a Call Graph document for this VB6 application.

{summary}

Show caller → callee relationships for EVERY method.
Identify: missing APIs, dead code, circular refs, broken dependencies.
Use exact method names. Do NOT invent anything."""
        return self.llm.call(prompt) or "# Call graph generation failed"

    def _gen_business_rules(self, summary: str, source: str) -> str:
        prompt = f"""Extract ALL business rules from this VB6 application.

{summary}

SOURCE CODE:
{source}

Document: validation rules, calculations, conditions, defaults, constraints.
Reference actual code. Mark uncertain rules with [NEEDS REVIEW]."""
        return self.llm.call(prompt) or "# Business rules extraction failed"

    def _gen_navigation(self, summary: str) -> str:
        prompt = f"""Generate UI Navigation document for this VB6 application.

{summary}

Document: entry point ({self.model.startup_form}), form-to-form transitions, modal/modeless usage,
data flow between forms, menu structure, user workflows.
Use actual form names. Do NOT invent forms."""
        return self.llm.call(prompt) or "# Navigation generation failed"

    def _gen_risk_report(self, summary: str) -> str:
        prompt = f"""Generate Risk Assessment for this VB6 migration.

{summary}

Include: unmapped controls, API dependencies, COM refs, complexity per form,
effort estimate (use {max(3, self.model.total_loc // 100)} days baseline),
GO/NO-GO recommendation. Be honest about gaps."""
        return self.llm.call(prompt) or "# Risk report generation failed"

    def _gen_migration_plan(self, summary: str) -> str:
        days = max(3, self.model.total_loc // 100)
        prompt = f"""Generate Migration Plan for this VB6 → VB.NET conversion.

{summary}

Baseline: {days} days. Per-form tasks. Specific control names. Testing strategy.
Be project-specific, not generic."""
        return self.llm.call(prompt) or "# Migration plan generation failed"


# ============================================================================
# LAMBDA HANDLER
# ============================================================================

def lambda_handler(event, context):
    try:
        logger.info("=== VB6 Phase 1 v5.0 — Semantic Analysis ===")

        record = event["Records"][0]
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        project_name = Path(key).stem

        logger.info(f"Processing: s3://{bucket}/{key}")

        # Extract
        zip_path = f"/tmp/{project_name}.zip"
        s3.download_file(bucket, key, zip_path)
        parser = VB6Parser()
        files = parser.extract_zip(zip_path)

        # Analyze — build complete semantic model
        logger.info("Building semantic model...")
        analyzer = SemanticAnalyzer(parser)
        model = analyzer.analyze(files, project_name)

        # Generate specs — all AI-powered
        logger.info("Generating specifications...")
        llm = LLM()
        gen = SpecGenerator(llm, model)
        docs = gen.generate_all()

        # Upload
        logger.info("Uploading...")
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        prefix = f"{project_name}/specification/{ts}"

        for filename, content in docs.items():
            ct = "application/json" if filename.endswith(".json") else "text/markdown"
            s3.put_object(Bucket=OUTPUT_BUCKET, Key=f"{prefix}/{filename}", Body=content, ContentType=ct)
            logger.info(f"  {filename} ({len(content)} chars)")

        logger.info("=== Phase 1 Complete ===")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "Phase 1 Complete",
                "project": project_name,
                "startup_form": model.startup_form,
                "loc": model.total_loc,
                "controls": model.total_controls,
                "events": model.total_events,
                "methods": model.total_methods,
                "risks": len(model.risks),
                "files": list(docs.keys()),
                "output": f"s3://{OUTPUT_BUCKET}/{prefix}/",
            }),
        }

    except Exception as e:
        logger.error(f"FATAL: {e}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
