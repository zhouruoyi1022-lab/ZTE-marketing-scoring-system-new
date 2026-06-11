"""
决赛打分系统 - Flask 单文件应用
中兴捧月营销大赛决赛打分系统
v1.0.0 - PRD适配版
"""

import os, json, csv, io, uuid, time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, session, jsonify, redirect, url_for, make_response
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "zte_finals_2025_secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "scoring.db"))
SESSION_LIFETIME = 8 * 3600  # 8小时（秒）
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=SESSION_LIFETIME)

# ─────────────────────────────────────────────────────────── DB ──
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            max_score REAL NOT NULL DEFAULT 10,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS examiners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role_type TEXT NOT NULL DEFAULT 'expert',
            password TEXT NOT NULL,
            username TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            session_token TEXT DEFAULT NULL,
            session_time TEXT DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT '',
            FOREIGN KEY(team_id) REFERENCES teams(id)
        );
        CREATE TABLE IF NOT EXISTS scores (
            examiner_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            stage_id INTEGER NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (examiner_id, team_id, stage_id)
        );
        CREATE TABLE IF NOT EXISTS weights (
            examiner_id INTEGER NOT NULL,
            stage_id INTEGER NOT NULL,
            weight_value REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (examiner_id, stage_id)
        );
        CREATE TABLE IF NOT EXISTS individual_reviews (
            examiner_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            overall_score REAL NOT NULL DEFAULT 0,
            highlight TEXT DEFAULT '',
            weakness TEXT DEFAULT '',
            intent TEXT DEFAULT '',
            work_location TEXT DEFAULT '',
            is_marketing_star INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (examiner_id, student_id)
        );
        """)
        # 兼容旧库：逐列检查并添加
        cols = [row[1] for row in db.execute("PRAGMA table_info(examiners)").fetchall()]
        if "username" not in cols:
            db.execute("ALTER TABLE examiners ADD COLUMN username TEXT DEFAULT ''")
        if "is_active" not in cols:
            db.execute("ALTER TABLE examiners ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "session_token" not in cols:
            db.execute("ALTER TABLE examiners ADD COLUMN session_token TEXT DEFAULT NULL")
        if "session_time" not in cols:
            db.execute("ALTER TABLE examiners ADD COLUMN session_time TEXT DEFAULT NULL")
        if "role_type" not in cols:
            # 迁移旧 role 字段到 role_type
            db.execute("ALTER TABLE examiners ADD COLUMN role_type TEXT DEFAULT 'expert'")
            # 尝试根据旧 role 字段值映射
            try:
                db.execute("UPDATE examiners SET role_type='cxo' WHERE role LIKE '%CEO%' OR role LIKE '%COO%' OR role LIKE '%CTO%' OR role LIKE '%CDO%' OR role LIKE '%CMO%'")
                db.execute("UPDATE examiners SET role_type='observer' WHERE role LIKE '%观察员%' OR role LIKE '%HR%'")
                db.execute("UPDATE examiners SET role_type='expert' WHERE role_type='expert'")
            except:
                pass
        if "role_display" not in cols:
            db.execute("ALTER TABLE examiners ADD COLUMN role_display TEXT DEFAULT ''")

        # 兼容旧 reviews 表：检查是否有 work_location 列
        review_cols = [row[1] for row in db.execute("PRAGMA table_info(individual_reviews)").fetchall()]
        if "work_location" not in review_cols:
            db.execute("ALTER TABLE individual_reviews ADD COLUMN work_location TEXT DEFAULT ''")
        if "intent" not in review_cols:
            # 旧表可能有 company_fit，新增 intent
            db.execute("ALTER TABLE individual_reviews ADD COLUMN intent TEXT DEFAULT ''")
        # 旧字段映射：strengths→highlight, weaknesses→weakness, company_fit→intent
        if "strengths" in review_cols and "highlight" not in review_cols:
            db.execute("ALTER TABLE individual_reviews ADD COLUMN highlight TEXT DEFAULT ''")
            db.execute("UPDATE individual_reviews SET highlight=strengths WHERE highlight='' AND strengths IS NOT NULL")
        if "company_fit" in review_cols and "intent" not in review_cols:
            db.execute("UPDATE individual_reviews SET intent=company_fit WHERE intent='' AND company_fit IS NOT NULL")
        if "recommend_star" in review_cols and "is_marketing_star" not in review_cols:
            db.execute("ALTER TABLE individual_reviews ADD COLUMN is_marketing_star INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE individual_reviews SET is_marketing_star=recommend_star")

SEED_MODE = os.environ.get("SEED_MODE", "official")  # "official" or "demo"

def seed_data():
    """Insert initial data (only when DB is empty)"""
    with get_db() as db:
        if db.execute("SELECT COUNT(*) FROM examiners").fetchone()[0] > 0:
            return
        if db.execute("SELECT COUNT(*) FROM stages").fetchone()[0] > 0:
            return

        # 初始化5个环节
        stages = [
            ("workshop", 10, 1),
            ("客户拜访", 20, 2),
            ("技术澄清", 30, 3),
            ("工服澄清", 25, 4),
            ("商务澄清", 15, 5),
        ]
        for sname, max_s, sort_o in stages:
            db.execute("INSERT INTO stages(name, max_score, sort_order) VALUES(?,?,?)", (sname, max_s, sort_o))

        if SEED_MODE == "official":
            _seed_official(db)
        else:
            _seed_demo(db)

def _seed_official(db):
    """Insert official competition data"""
    teams = [
        ("星云通讯", "A", [("赵苑婷","MKT商务",True),("杨冰","MKT技术",False),("刘汀滢","客户经理",False),("李明望","项目交付经理",False),("吴聿斌","项目交付经理",False),("杨金奇","客户经理",False)]),
        ("云雀通讯", "B", [("雷湘豫","网络技术工程师",True),("王浩洁","客户经理",False),("鹿驰","项目交付经理",False),("李佳蓉","MKT商务",False),("戴正康","网络技术工程师",False),("胡清源","MKT技术",False)]),
        ("兴火通讯", "C", [("杨浩城","MKT技术",True),("刘雨恒","MKT商务",False),("赵彦博","客户经理",False),("张嘉文","项目交付经理",False),("魏菲","MKT商务",False),("史英辰","网络技术工程师",False)]),
        ("汇科通讯", "D", [("史明鉴","客户经理",True),("胡泽林","网络技术工程师",False),("刘翀","项目交付经理",False),("冯伊涵","MKT商务",False),("沈武琦","MKT技术",False),("付家俊","MKT技术",False)]),
        ("辰钧科技", "E", [("张佳杰","客户经理",True),("薛博文","客户经理",False),("李娜","MKT商务",False),("潘峰","项目交付经理",False),("王柯程","MKT技术",False),("刘翔宇","网络技术工程师",False)]),
        ("星翊科技", "F", [("冉健","MKT技术",True),("李天娇","客户经理",False),("蒋沁宏","MKT技术",False),("丁俊杰","MKT商务",False),("田凌","项目交付经理",False),("王振耀","网络技术工程师",False)]),
        ("莲芯科技", "G", [("商煜航","MKT商务",True),("黄守缘","客户经理",False),("何孟谦","客户经理",False),("吴梓珩","MKT技术",False),("黄筠杰","网络技术工程师",False),("孙琦","项目交付经理",False)]),
        ("诺瓦科技", "H", [("庄智杰","客户经理",True),("柳玥彤","客户经理",False),("李子君","项目交付经理",False),("李明泉","网络技术工程师",False),("王宇","MKT技术",False),("高泽","MKT商务",False)]),
    ]
    # 评委：(姓名, 角色类型, 登录密码/工号)
    # role_type: cxo / expert / observer
    examiners = [
        ("胡雪梅","cxo","00011826"),("段玉龙","cxo","10109111"),("张健","cxo","10139322"),
        ("段华伟","cxo","10068324"),("刘军","cxo","10028771"),("巨洋","cxo","00030479"),
        ("乔元蕾","cxo","00015084"),("沙立尔特","cxo","00293737"),
        ("周昭","cxo","00090449"),("杨萌","cxo","00333465"),
        ("郭紫薇","observer","00323116"),("席丁香","observer","00223437"),
        ("赵恒","observer","10157949"),("杨瑞","observer","00225931"),
        ("吴峰","expert","10002931"),("李燕","expert","00192432"),
        ("王亮光","expert","10024308"),("李宁","expert","00054393"),
    ]
    for (tname, tcode, students) in teams:
        cur = db.execute("INSERT INTO teams(name,code) VALUES(?,?)", (tname, tcode))
        tid = cur.lastrowid
        for (sname, srole, is_captain) in students:
            display = f"{sname}（队长）" if is_captain else sname
            db.execute("INSERT INTO students(team_id,name,role) VALUES(?,?,?)", (tid, display, srole))
    db.executemany(
        "INSERT INTO examiners(username,name,role_type,password) VALUES(?,?,?,?)",
        [(n, n, rt, p) for n, rt, p in examiners]
    )
    print("[OK] Official data seeded: 8 teams, 48 students, 18 examiners, 5 stages")

def _seed_demo(db):
    """Insert demo data for testing"""
    examiners = [
        ("张总","cxo","e001"),("李总","cxo","e002"),("王专家","expert","e003"),
        ("赵专家","expert","e004"),("陈HR","observer","e005"),("刘HR","observer","e006"),
        ("孙专家","expert","e007"),("周专家","expert","e008"),
        ("吴总","cxo","e009"),("郑专家","expert","e010"),
    ]
    db.executemany("INSERT INTO examiners(username,name,role_type,password) VALUES(?,?,?,?)",
                   [(n,n,rt,p) for n,rt,p in examiners])
    teams = [("天狼队","A"),("猎户队","B"),("北斗队","C"),("天鹰队","D"),
             ("飞龙队","E"),("白虎队","F"),("青龙队","G"),("玄武队","H")]
    db.executemany("INSERT INTO teams(name,code) VALUES(?,?)", teams)
    roles = ["队长/项目经理","技术负责人","商务负责人","运营负责人","财务负责人","展示负责人"]
    team_rows = db.execute("SELECT id,name FROM teams ORDER BY id").fetchall()
    for t in team_rows:
        for i, role in enumerate(roles):
            db.execute("INSERT INTO students(team_id,name,role) VALUES(?,?,?)",
                       (t["id"], f"{t['name']}·选手{i+1}", role))
    print("[OK] Demo data seeded")

# ───────────────────────────────────────────────── Auth helpers ──
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 管理员通过 is_admin 标记
        if session.get("is_admin"):
            return f(*args, **kwargs)
        # 评委需要验证 session_token
        eid = session.get("examiner_id")
        if not eid:
            return jsonify({"error": "请先登录"}), 401
        with get_db() as db:
            examiner = db.execute("SELECT session_token, session_time, is_active FROM examiners WHERE id=?", (eid,)).fetchone()
        if not examiner:
            session.clear()
            return jsonify({"error": "账号不存在"}), 401
        if not examiner["is_active"]:
            session.clear()
            return jsonify({"error": "账号已被禁用"}), 401
        # 踢旧登录：检查 session_token 是否匹配
        if examiner["session_token"] and examiner["session_token"] != session.get("session_token"):
            session.clear()
            return jsonify({"error": "账号已在其他设备登录"}), 401
        # 检查 Session 超时
        if examiner["session_time"]:
            try:
                login_time = datetime.fromisoformat(examiner["session_time"])
                if (datetime.now() - login_time).total_seconds() > SESSION_LIFETIME:
                    session.clear()
                    return jsonify({"error": "登录已过期，请重新登录"}), 401
            except:
                pass
        return f(*args, **kwargs)
    return decorated

def examiner_required(f):
    """评委专用接口（需要examiner_id且非管理员）"""
    @wraps(f)
    def decorated(*args, **kwargs):
        eid = session.get("examiner_id")
        if not eid or eid == "__admin__":
            return jsonify({"error": "请先以评委身份登录"}), 401
        # 复用 login_required 的验证逻辑
        with get_db() as db:
            examiner = db.execute("SELECT session_token, is_active, role_type FROM examiners WHERE id=?", (eid,)).fetchone()
        if not examiner or not examiner["is_active"]:
            session.clear()
            return jsonify({"error": "账号不可用"}), 401
        if examiner["session_token"] and examiner["session_token"] != session.get("session_token"):
            session.clear()
            return jsonify({"error": "账号已在其他设备登录"}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "需要管理员权限"}), 403
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────────── API Routes ──

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json
    username = (data.get("username") or "").strip()
    pid = (data.get("password") or "").strip()
    with get_db() as db:
        examiner = db.execute(
            "SELECT * FROM examiners WHERE username=? AND password=?", (username, pid)
        ).fetchone()
    if not examiner:
        return jsonify({"error": "用户名或密码错误"}), 401
    if not examiner["is_active"]:
        return jsonify({"error": "账号已被禁用，请联系管理员"}), 401
    # 踢旧登录：生成新 session_token
    token = uuid.uuid4().hex
    now = datetime.now().isoformat()
    with get_db() as db:
        db.execute("UPDATE examiners SET session_token=?, session_time=? WHERE id=?",
                   (token, now, examiner["id"]))
    session["examiner_id"] = examiner["id"]
    session["examiner_name"] = examiner["name"]
    session["session_token"] = token
    session.permanent = True
    return jsonify({
        "id": examiner["id"],
        "name": examiner["name"],
        "role_type": examiner["role_type"],
        "is_observer": examiner["role_type"] == "observer"
    })

@app.route("/api/admin_login", methods=["POST"])
def api_admin_login():
    data = request.json
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if username != "管理员":
        return jsonify({"error": "用户名或密码错误"}), 401
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "用户名或密码错误"}), 401
    session["is_admin"] = True
    session.permanent = True
    if not session.get("examiner_id"):
        session["examiner_id"] = "__admin__"
        session["examiner_name"] = "管理员"
    return jsonify({"ok": True, "examiner_name": session["examiner_name"]})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    eid = session.get("examiner_id")
    if eid and eid != "__admin__":
        with get_db() as db:
            db.execute("UPDATE examiners SET session_token=NULL, session_time=NULL WHERE id=?", (eid,))
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def api_me():
    eid = session.get("examiner_id")
    is_admin = session.get("is_admin", False)
    if eid and eid != "__admin__":
        with get_db() as db:
            ex = db.execute("SELECT name, role_type, is_active FROM examiners WHERE id=?", (eid,)).fetchone()
        if not ex or not ex["is_active"]:
            session.clear()
            return jsonify({"examiner_id": None, "is_admin": False})
        return jsonify({
            "examiner_id": eid,
            "examiner_name": ex["name"],
            "role_type": ex["role_type"],
            "is_observer": ex["role_type"] == "observer",
            "is_admin": is_admin
        })
    if is_admin:
        return jsonify({
            "examiner_id": "__admin__",
            "examiner_name": "管理员",
            "is_admin": True
        })
    return jsonify({"examiner_id": None, "is_admin": False})

# ─── Stages (环节)
@app.route("/api/stages")
@login_required
def api_stages():
    with get_db() as db:
        stages = [dict(r) for r in db.execute("SELECT * FROM stages ORDER BY sort_order").fetchall()]
    return jsonify(stages)

@app.route("/api/admin/stages/<int:stage_id>", methods=["PUT"])
@login_required
@admin_required
def api_update_stage(stage_id):
    data = request.json
    name = (data.get("name") or "").strip()
    max_score = data.get("max_score")
    if name:
        with get_db() as db:
            db.execute("UPDATE stages SET name=? WHERE id=?", (name, stage_id))
    if max_score is not None:
        max_score = float(max_score)
        if max_score <= 0:
            return jsonify({"error": "满分必须大于0"}), 400
        with get_db() as db:
            db.execute("UPDATE stages SET max_score=? WHERE id=?", (max_score, stage_id))
    return jsonify({"ok": True})

# ─── Teams & Students
@app.route("/api/teams")
@login_required
def api_teams():
    with get_db() as db:
        teams = [dict(r) for r in db.execute("SELECT * FROM teams ORDER BY code").fetchall()]
    return jsonify(teams)

@app.route("/api/students")
@login_required
def api_students():
    team_id = request.args.get("team_id")
    with get_db() as db:
        if team_id:
            students = [dict(r) for r in db.execute(
                "SELECT s.*, t.name as team_name FROM students s JOIN teams t ON s.team_id=t.id WHERE s.team_id=? ORDER BY s.id",
                (team_id,)
            ).fetchall()]
        else:
            students = [dict(r) for r in db.execute(
                "SELECT s.*, t.name as team_name FROM students s JOIN teams t ON s.team_id=t.id ORDER BY t.code, s.id"
            ).fetchall()]
    return jsonify(students)

# ─── Scores (评委打分 - 新模型)
@app.route("/api/scores", methods=["GET"])
@examiner_required
def api_get_scores():
    """获取当前评委的所有打分"""
    eid = session["examiner_id"]
    with get_db() as db:
        rows = db.execute(
            "SELECT team_id, stage_id, score FROM scores WHERE examiner_id=?", (eid,)
        ).fetchall()
    # 返回 {stage_id: {team_id: score}}
    result = {}
    for r in rows:
        result.setdefault(str(r["stage_id"]), {})[str(r["team_id"])] = r["score"]
    return jsonify(result)

@app.route("/api/score", methods=["POST"])
@examiner_required
def api_submit_score():
    """评委提交单个打分（Upsert）"""
    data = request.json
    team_id = data.get("team_id")
    stage_id = data.get("stage_id")
    score = data.get("score")
    eid = session["examiner_id"]

    if team_id is None or stage_id is None or score is None:
        return jsonify({"error": "缺少必要参数"}), 400

    # 检查环节满分
    with get_db() as db:
        stage = db.execute("SELECT max_score FROM stages WHERE id=?", (stage_id,)).fetchone()
    if not stage:
        return jsonify({"error": "环节不存在"}), 404

    score = float(score)
    max_score = stage["max_score"]
    if score < 0 or score > max_score:
        return jsonify({"error": f"分数必须在0~{max_score}之间"}), 400

    now = datetime.now().isoformat()
    with get_db() as db:
        db.execute(
            """INSERT INTO scores(examiner_id, team_id, stage_id, score, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(examiner_id, team_id, stage_id) DO UPDATE SET score=excluded.score, updated_at=excluded.updated_at""",
            (eid, team_id, stage_id, score, now)
        )
    return jsonify({"ok": True})

@app.route("/api/scores/batch", methods=["POST"])
@examiner_required
def api_submit_scores_batch():
    """评委批量提交打分"""
    data = request.json  # [{team_id, stage_id, score}, ...]
    if not isinstance(data, list):
        return jsonify({"error": "数据格式错误"}), 400
    eid = session["examiner_id"]
    now = datetime.now().isoformat()

    with get_db() as db:
        # 预加载所有环节满分
        stages = {r["id"]: r["max_score"] for r in db.execute("SELECT id, max_score FROM stages").fetchall()}
        for item in data:
            team_id = item.get("team_id")
            stage_id = item.get("stage_id")
            score = item.get("score")
            if team_id is None or stage_id is None or score is None:
                continue
            score = float(score)
            max_s = stages.get(stage_id, 0)
            if score < 0 or score > max_s:
                continue  # 跳过无效分数
            db.execute(
                """INSERT INTO scores(examiner_id, team_id, stage_id, score, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(examiner_id, team_id, stage_id) DO UPDATE SET score=excluded.score, updated_at=excluded.updated_at""",
                (eid, team_id, stage_id, score, now)
            )
    return jsonify({"ok": True})

# ─── Individual reviews (评委点评 - 新模型)
@app.route("/api/reviews", methods=["GET"])
@examiner_required
def api_get_reviews():
    student_id = request.args.get("student_id")
    eid = session["examiner_id"]
    with get_db() as db:
        if student_id:
            row = db.execute(
                "SELECT * FROM individual_reviews WHERE examiner_id=? AND student_id=?",
                (eid, student_id)
            ).fetchone()
            return jsonify(dict(row) if row else None)
        else:
            rows = db.execute(
                "SELECT * FROM individual_reviews WHERE examiner_id=?", (eid,)
            ).fetchall()
            return jsonify({r["student_id"]: dict(r) for r in rows})

@app.route("/api/reviews", methods=["POST"])
@examiner_required
def api_submit_review():
    data = request.json
    student_id = data.get("student_id")
    overall = float(data.get("overall_score", 0))
    if not (1 <= overall <= 5):
        return jsonify({"error": "整体评分需在1-5之间"}), 400

    highlight = (data.get("highlight") or "")[:500]
    weakness = (data.get("weakness") or "")[:500]
    intent = (data.get("intent") or "")[:500]
    work_location = (data.get("work_location") or "").strip()
    if work_location and work_location not in ("国内", "海外"):
        return jsonify({"error": "推荐工作地只能选择 国内 或 海外"}), 400
    is_star = 1 if data.get("is_marketing_star") else 0
    eid = session["examiner_id"]

    # 推荐营销之星上限检查
    if is_star:
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM individual_reviews WHERE examiner_id=? AND is_marketing_star=1 AND student_id!=?",
                (eid, student_id)
            ).fetchone()[0]
        if count >= 6:
            return jsonify({"error": "每位评委最多推荐6名营销之星"}), 400

    now = datetime.now().isoformat()
    with get_db() as db:
        if not db.execute("SELECT id FROM students WHERE id=?", (student_id,)).fetchone():
            return jsonify({"error": "选手不存在"}), 404
        db.execute(
            """INSERT INTO individual_reviews(examiner_id, student_id, overall_score, highlight, weakness, intent, work_location, is_marketing_star, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(examiner_id, student_id) DO UPDATE SET
                 overall_score=excluded.overall_score, highlight=excluded.highlight,
                 weakness=excluded.weakness, intent=excluded.intent,
                 work_location=excluded.work_location, is_marketing_star=excluded.is_marketing_star,
                 updated_at=excluded.updated_at""",
            (eid, student_id, overall, highlight, weakness, intent, work_location, is_star, now)
        )
    return jsonify({"ok": True})

@app.route("/api/reviews/star_count")
@examiner_required
def api_review_star_count():
    """当前评委已推荐营销之星人数"""
    eid = session["examiner_id"]
    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM individual_reviews WHERE examiner_id=? AND is_marketing_star=1",
            (eid,)
        ).fetchone()[0]
    return jsonify({"count": count, "max": 6})

# ─── Score calculation (加权分数)
def calculate_team_scores():
    """
    计算团队决赛总分

    公式：
      某环节实际得分 = Σ(评委打分 × 评委权重)   [仅计权重>0的评委]
      队伍决赛总分   = Σ(各环节实际得分)

    各环节满分之和 = 100，因此总分天然为百分制，无需额外归一化。
    """
    with get_db() as db:
        teams = {r["id"]: dict(r) for r in db.execute("SELECT * FROM teams ORDER BY code").fetchall()}
        stages = [dict(r) for r in db.execute("SELECT * FROM stages ORDER BY sort_order").fetchall()]
        all_scores = db.execute("""
            SELECT s.examiner_id, s.team_id, s.stage_id, s.score
            FROM scores s
            JOIN examiners e ON s.examiner_id = e.id AND e.is_active = 1
        """).fetchall()
        all_weights = db.execute("SELECT examiner_id, stage_id, weight_value FROM weights").fetchall()

    # 构建权重索引: {(examiner_id, stage_id): weight_value}
    weight_map = {}
    for w in all_weights:
        weight_map[(w["examiner_id"], w["stage_id"])] = w["weight_value"]

    # 按环节计算每支队伍的加权得分：Σ(score × weight)
    stage_scores = {st["id"]: {tid: 0.0 for tid in teams} for st in stages}
    # 统计每个环节中对该队伍实际打分的评委人数
    stage_judge_cnt = {st["id"]: {tid: 0 for tid in teams} for st in stages}

    for row in all_scores:
        eid = row["examiner_id"]
        tid = row["team_id"]
        sid = row["stage_id"]
        score = row["score"]
        w = weight_map.get((eid, sid), 0)
        if w > 0:
            stage_scores[sid][tid] += score * w
            stage_judge_cnt[sid][tid] += 1

    results = []
    for tid, team in teams.items():
        total_score = 0.0
        entry = dict(team)
        for st in stages:
            sid = st["id"]
            actual = stage_scores[sid].get(tid, 0)  # 该环节实际得分 = Σ(score × weight)
            entry[f"stage_{sid}"] = round(actual, 4)
            entry[f"stage_{sid}_pct"] = round(actual, 2)  # 保留字段兼容前端，值=actual
            entry[f"stage_{sid}_judges"] = stage_judge_cnt[sid].get(tid, 0)
            total_score += actual

        entry["total_score"] = round(total_score, 2)
        results.append(entry)

    # 按总分降序排列
    results.sort(key=lambda x: -x["total_score"])
    for i, r in enumerate(results):
        r["rank"] = i + 1
    return results, stages

def calculate_star_scores():
    """计算营销之星排名"""
    with get_db() as db:
        students = {r["id"]: dict(r) for r in db.execute(
            "SELECT s.*, t.name as team_name FROM students s JOIN teams t ON s.team_id=t.id"
        ).fetchall()}
        reviews = db.execute("""
            SELECT ir.* FROM individual_reviews ir
            JOIN examiners e ON ir.examiner_id = e.id
            WHERE e.is_active = 1
        """).fetchall()

    score_sum = {sid: 0.0 for sid in students}
    score_cnt = {sid: 0 for sid in students}
    recommend_cnt = {sid: 0 for sid in students}

    for row in reviews:
        sid = row["student_id"]
        score_sum[sid] += row["overall_score"]
        score_cnt[sid] += 1
        recommend_cnt[sid] += row["is_marketing_star"]

    results = []
    for sid, student in students.items():
        cnt = score_cnt[sid]
        avg = score_sum[sid] / cnt if cnt > 0 else 0.0
        rec = recommend_cnt[sid]
        entry = dict(student)
        entry["avg_score"] = round(avg, 2)
        entry["recommend_count"] = rec
        entry["review_count"] = cnt
        results.append(entry)

    results.sort(key=lambda x: (-x["recommend_count"], -x["avg_score"]))
    for i, r in enumerate(results):
        r["star_rank"] = i + 1
    return results

@app.route("/api/scores/teams")
@login_required
def api_team_scores():
    results, stages = calculate_team_scores()
    return jsonify({"teams": results, "stages": [{"id": s["id"], "name": s["name"], "max_score": s["max_score"]} for s in stages]})

@app.route("/api/scores/stars")
@login_required
def api_star_scores():
    return jsonify(calculate_star_scores()[:10])

# ─── Weights API (评委×环节矩阵)
@app.route("/api/weights/matrix", methods=["GET"])
@login_required
@admin_required
def api_get_weights_matrix():
    """返回权重矩阵: {examiner_id: {stage_id: weight_value(0~1)}}"""
    with get_db() as db:
        weights = db.execute("SELECT examiner_id, stage_id, weight_value FROM weights").fetchall()
        examiners = [dict(r) for r in db.execute("SELECT id, name, role_type FROM examiners WHERE is_active=1 ORDER BY id").fetchall()]
        stages = [dict(r) for r in db.execute("SELECT id, name, max_score FROM stages ORDER BY sort_order").fetchall()]

    # 构建矩阵，直接返回0~1小数
    matrix = {}
    for w in weights:
        eid = str(w["examiner_id"])
        sid = str(w["stage_id"])
        matrix.setdefault(eid, {})[sid] = round(w["weight_value"], 6)

    return jsonify({"matrix": matrix, "examiners": examiners, "stages": stages})

@app.route("/api/weights/matrix", methods=["POST"])
@login_required
@admin_required
def api_save_weights_matrix():
    """保存权重矩阵: {examiner_id: {stage_id: weight_value(0~1)}}"""
    data = request.json
    matrix = data.get("matrix", {})
    now = datetime.now().isoformat()

    # 校验每环节权重和是否=1
    with get_db() as db:
        stages = [dict(r) for r in db.execute("SELECT id, name FROM stages ORDER BY sort_order").fetchall()]
        examiners = [dict(r) for r in db.execute("SELECT id, name FROM examiners WHERE is_active=1").fetchall()]
        examiner_ids = {e["id"] for e in examiners}

    warnings = []
    for stage in stages:
        sid = str(stage["id"])
        total = 0
        for eid_str, weights in matrix.items():
            if int(eid_str) not in examiner_ids:
                continue
            val = weights.get(sid)
            if val is not None and val != "":
                try:
                    total += float(val)
                except:
                    pass
        if total > 0 and abs(total - 1) > 0.0001:
            warnings.append(f"环节「{stage['name']}」权重之和为 {total:.4f}，不等于 1")

    # 先删除旧权重，再批量插入
    with get_db() as db:
        db.execute("DELETE FROM weights")
        for eid_str, weights in matrix.items():
            eid = int(eid_str)
            if eid not in examiner_ids:
                continue
            for sid_str, val in weights.items():
                sid = int(sid_str)
                if val is not None and val != "":
                    try:
                        w_val = float(val)  # 前端直接传0~1小数
                        if w_val < 0 or w_val > 1:
                            continue
                        db.execute(
                            """INSERT INTO weights(examiner_id, stage_id, weight_value, updated_at)
                               VALUES(?,?,?,?)
                               ON CONFLICT(examiner_id, stage_id) DO UPDATE SET weight_value=excluded.weight_value, updated_at=excluded.updated_at""",
                            (eid, sid, w_val, now)
                        )
                    except:
                        continue

    return jsonify({"ok": True, "warnings": warnings})

# ─── Admin: Edit teams
@app.route("/api/admin/teams/<int:team_id>", methods=["PUT"])
@login_required
@admin_required
def api_update_team(team_id):
    data = request.json
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()
    if not name:
        return jsonify({"error": "队伍名称不能为空"}), 400
    with get_db() as db:
        if not db.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone():
            return jsonify({"error": "队伍不存在"}), 404
        if code:
            conflict = db.execute("SELECT id FROM teams WHERE code=? AND id!=?", (code, team_id)).fetchone()
            if conflict:
                return jsonify({"error": f"编号 {code} 已被其他队伍使用"}), 400
            db.execute("UPDATE teams SET name=?, code=? WHERE id=?", (name, code, team_id))
        else:
            db.execute("UPDATE teams SET name=? WHERE id=?", (name, team_id))
    return jsonify({"ok": True})

# ─── Admin: Edit students
@app.route("/api/admin/students/<int:student_id>", methods=["PUT"])
@login_required
@admin_required
def api_update_student(student_id):
    data = request.json
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "").strip()
    if not name:
        return jsonify({"error": "选手姓名不能为空"}), 400
    with get_db() as db:
        if not db.execute("SELECT id FROM students WHERE id=?", (student_id,)).fetchone():
            return jsonify({"error": "选手不存在"}), 404
        db.execute("UPDATE students SET name=?, role=? WHERE id=?", (name, role, student_id))
    return jsonify({"ok": True})

@app.route("/api/admin/students", methods=["POST"])
@login_required
@admin_required
def api_add_student():
    data = request.json
    team_id = data.get("team_id")
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "").strip()
    if not name or not team_id:
        return jsonify({"error": "队伍ID和选手姓名为必填"}), 400
    with get_db() as db:
        if not db.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone():
            return jsonify({"error": "队伍不存在"}), 404
        cur = db.execute("INSERT INTO students(team_id,name,role) VALUES(?,?,?)", (team_id, name, role))
        new_id = cur.lastrowid
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/admin/students/<int:student_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_student(student_id):
    with get_db() as db:
        if not db.execute("SELECT id FROM students WHERE id=?", (student_id,)).fetchone():
            return jsonify({"error": "选手不存在"}), 404
        db.execute("DELETE FROM students WHERE id=?", (student_id,))
        db.execute("DELETE FROM individual_reviews WHERE student_id=?", (student_id,))
    return jsonify({"ok": True})

# ─── Admin: Examiners
@app.route("/api/admin/examiners", methods=["GET"])
@login_required
@admin_required
def api_get_examiners():
    with get_db() as db:
        rows = [dict(r) for r in db.execute("SELECT id, name, role_type, role_display, password, username, is_active FROM examiners ORDER BY id").fetchall()]
    return jsonify(rows)

@app.route("/api/admin/examiners/<int:examiner_id>", methods=["PUT"])
@login_required
@admin_required
def api_update_examiner(examiner_id):
    data = request.json
    username = (data.get("username") or "").strip()
    name = (data.get("name") or "").strip()
    role_type = (data.get("role_type") or "").strip()
    role_display = (data.get("role_display") or "").strip()
    password = (data.get("password") or "").strip()
    is_active = data.get("is_active")
    if not name:
        return jsonify({"error": "评委姓名不能为空"}), 400
    if role_type not in ("cxo", "expert", "observer"):
        return jsonify({"error": "角色类型必须为 cxo/expert/observer"}), 400
    with get_db() as db:
        if not db.execute("SELECT id FROM examiners WHERE id=?", (examiner_id,)).fetchone():
            return jsonify({"error": "评委不存在"}), 404
        if password:
            conflict = db.execute("SELECT id FROM examiners WHERE password=? AND id!=?", (password, examiner_id)).fetchone()
            if conflict:
                return jsonify({"error": "该密码已被其他评委使用"}), 400
            db.execute("UPDATE examiners SET username=?, name=?, role_type=?, role_display=?, password=? WHERE id=?",
                       (username or name, name, role_type, role_display, password, examiner_id))
        else:
            db.execute("UPDATE examiners SET username=?, name=?, role_type=?, role_display=? WHERE id=?",
                       (username or name, name, role_type, role_display, examiner_id))
        if is_active is not None:
            db.execute("UPDATE examiners SET is_active=? WHERE id=?", (1 if is_active else 0, examiner_id))
    return jsonify({"ok": True})

@app.route("/api/admin/examiners", methods=["POST"])
@login_required
@admin_required
def api_add_examiner():
    data = request.json
    username = (data.get("username") or "").strip()
    name = (data.get("name") or "").strip()
    role_type = (data.get("role_type") or "expert").strip()
    role_display = (data.get("role_display") or "").strip()
    password = (data.get("password") or "").strip()
    if not name or not password:
        return jsonify({"error": "姓名和密码为必填"}), 400
    if role_type not in ("cxo", "expert", "observer"):
        role_type = "expert"
    if not username:
        username = name
    with get_db() as db:
        conflict = db.execute("SELECT id FROM examiners WHERE password=?", (password,)).fetchone()
        if conflict:
            return jsonify({"error": "该密码已被使用"}), 400
        cur = db.execute("INSERT INTO examiners(username,name,role_type,role_display,password) VALUES(?,?,?,?,?)",
                         (username, name, role_type, role_display, password))
    return jsonify({"ok": True, "id": cur.lastrowid})

@app.route("/api/admin/examiners/<int:examiner_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_examiner(examiner_id):
    with get_db() as db:
        db.execute("DELETE FROM examiners WHERE id=?", (examiner_id,))
        db.execute("DELETE FROM scores WHERE examiner_id=?", (examiner_id,))
        db.execute("DELETE FROM weights WHERE examiner_id=?", (examiner_id,))
        db.execute("DELETE FROM individual_reviews WHERE examiner_id=?", (examiner_id,))
    return jsonify({"ok": True})

@app.route("/api/admin/examiners/<int:examiner_id>/reset_password", methods=["POST"])
@login_required
@admin_required
def api_reset_examiner_password(examiner_id):
    """重置评委密码"""
    data = request.json
    new_password = (data.get("password") or "").strip()
    if not new_password:
        return jsonify({"error": "新密码不能为空"}), 400
    with get_db() as db:
        if not db.execute("SELECT id FROM examiners WHERE id=?", (examiner_id,)).fetchone():
            return jsonify({"error": "评委不存在"}), 404
        conflict = db.execute("SELECT id FROM examiners WHERE password=? AND id!=?", (new_password, examiner_id)).fetchone()
        if conflict:
            return jsonify({"error": "该密码已被其他评委使用"}), 400
        db.execute("UPDATE examiners SET password=? WHERE id=?", (new_password, examiner_id))
        # 重置密码后踢掉旧登录
        db.execute("UPDATE examiners SET session_token=NULL, session_time=NULL WHERE id=?", (examiner_id,))
    return jsonify({"ok": True})

# ─── Admin progress
@app.route("/api/admin/progress")
@login_required
@admin_required
def api_admin_progress():
    with get_db() as db:
        examiners = [dict(r) for r in db.execute("SELECT id, name, role_type, role_display FROM examiners WHERE is_active=1 ORDER BY id")]
        stages = [dict(r) for r in db.execute("SELECT id, name FROM stages ORDER BY sort_order")]
        # 每位评委在每个环节是否打过分
        score_progress = db.execute("""
            SELECT examiner_id, stage_id, COUNT(DISTINCT team_id) as team_count
            FROM scores
            GROUP BY examiner_id, stage_id
        """).fetchall()
        review_cnt = db.execute(
            "SELECT examiner_id, COUNT(*) as cnt FROM individual_reviews GROUP BY examiner_id"
        ).fetchall()
        total_teams = db.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]

    # 构建进度索引
    score_map = {}  # {(examiner_id, stage_id): team_count}
    for row in score_progress:
        score_map[(row["examiner_id"], row["stage_id"])] = row["team_count"]

    review_map = {r["examiner_id"]: r["cnt"] for r in review_cnt}

    for e in examiners:
        e["stage_progress"] = []
        for st in stages:
            cnt = score_map.get((e["id"], st["id"]), 0)
            e["stage_progress"].append({
                "stage_id": st["id"],
                "stage_name": st["name"],
                "submitted": cnt >= total_teams,
                "team_count": cnt,
                "total_teams": total_teams
            })
        e["review_count"] = review_map.get(e["id"], 0)
        e["review_total"] = total_students
    return jsonify(examiners)

# ─── Export
@app.route("/api/export/scores")
@login_required
@admin_required
def export_scores():
    """导出团队打分表 - PRD二级列头格式，含评委角色行和权重行"""
    with get_db() as db:
        teams = [dict(r) for r in db.execute("SELECT id, name, code FROM teams ORDER BY code").fetchall()]
        stages = [dict(r) for r in db.execute("SELECT id, name, max_score FROM stages ORDER BY sort_order").fetchall()]
        examiners = [dict(r) for r in db.execute("SELECT id, name, role_type, role_display FROM examiners WHERE is_active=1 ORDER BY id").fetchall()]
        scores = db.execute("""
            SELECT s.examiner_id, s.team_id, s.stage_id, s.score
            FROM scores s
            JOIN examiners e ON s.examiner_id = e.id
            WHERE e.is_active = 1
            ORDER BY s.stage_id, s.examiner_id
        """).fetchall()
        weights = db.execute("SELECT examiner_id, stage_id, weight_value FROM weights").fetchall()

    # 构建分数索引: {(examiner_id, team_id, stage_id): score}
    score_map = {}
    for s in scores:
        score_map[(s["examiner_id"], s["team_id"], s["stage_id"])] = s["score"]

    # 构建权重索引: {(examiner_id, stage_id): weight_value}
    weight_map = {}
    for wt in weights:
        weight_map[(wt["examiner_id"], wt["stage_id"])] = wt["weight_value"]

    # 角色显示辅助：优先role_display，为空则回退role_type中文映射
    role_cn = {"cxo": "CXO", "expert": "评审专家", "observer": "观察员"}

    def display_role(ex):
        return ex["role_display"] if ex.get("role_display") else role_cn.get(ex["role_type"], ex["role_type"])

    buf = io.StringIO()
    # BOM for Excel
    buf.write('\ufeff')
    w = csv.writer(buf)

    # 二级列头
    header1 = ["评委姓名"]  # 第一列改为"评委姓名"
    header2 = ["评委姓名"]
    for st in stages:
        for ex in examiners:
            header1.append(st["name"])
            header2.append(ex["name"])
    w.writerow(header1)
    w.writerow(header2)

    # 评委角色行：表头之后、权重行之前
    role_row = ["评委角色"]
    for st in stages:
        for ex in examiners:
            role_row.append(display_role(ex))
    w.writerow(role_row)

    # 权重行：评委角色行之后、数据行之前
    weight_row = ["权重"]
    for st in stages:
        for ex in examiners:
            wv = weight_map.get((ex["id"], st["id"]))
            weight_row.append(f"{wv:.2f}" if wv is not None else "0.00")
    w.writerow(weight_row)

    # 数据行
    for team in teams:
        row = [team["name"]]
        for st in stages:
            for ex in examiners:
                score = score_map.get((ex["id"], team["id"], st["id"]))
                row.append(score if score is not None else "")
        w.writerow(row)

    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    resp.headers["Content-Disposition"] = f"attachment; filename=team_scores_{now_str}.csv"
    return resp

@app.route("/api/export/reviews")
@login_required
@admin_required
def export_reviews():
    """导出个人点评表 - PRD二级列头格式"""
    with get_db() as db:
        students = [dict(r) for r in db.execute("""
            SELECT s.id, s.name, s.role, t.name as team_name, t.code as team_code
            FROM students s JOIN teams t ON s.team_id=t.id
            ORDER BY t.code, s.id
        """).fetchall()]
        examiners = [dict(r) for r in db.execute("SELECT id, name, role_type FROM examiners WHERE is_active=1 ORDER BY id").fetchall()]
        reviews = db.execute("""
            SELECT ir.* FROM individual_reviews ir
            JOIN examiners e ON ir.examiner_id = e.id
            WHERE e.is_active = 1
        """).fetchall()

    # 构建点评索引: {(examiner_id, student_id): review}
    review_map = {}
    for r in reviews:
        review_map[(r["examiner_id"], r["student_id"])] = dict(r)

    buf = io.StringIO()
    buf.write('\ufeff')
    w = csv.writer(buf)

    # 二级列头
    header1 = ["", ""]
    header2 = ["队伍", "选手"]
    score_fields = ["整体评估", "亮点", "不足", "意向签约", "推荐工作地", "是否推荐营销之星"]
    for ex in examiners:
        for field in score_fields:
            header1.append(ex["name"])
            header2.append(field)
    w.writerow(header1)
    w.writerow(header2)

    # 数据行
    for student in students:
        row = [student["team_name"], student["name"]]
        for ex in examiners:
            rev = review_map.get((ex["id"], student["id"]))
            if rev:
                row.append(rev["overall_score"])
                row.append(rev["highlight"])
                row.append(rev["weakness"])
                row.append(rev["intent"])
                row.append(rev.get("work_location", ""))
                row.append("是" if rev.get("is_marketing_star") else "否")
            else:
                row.extend([""] * 6)
        w.writerow(row)

    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    resp.headers["Content-Disposition"] = f"attachment; filename=individual_reviews_{now_str}.csv"
    return resp

@app.route("/api/admin/clear_scores", methods=["POST"])
@login_required
@admin_required
def clear_scores():
    """清空所有打分数据，保留队伍/选手/评委信息不变"""
    with get_db() as db:
        db.execute("DELETE FROM scores")
        db.execute("DELETE FROM individual_reviews")
    return jsonify({"ok": True, "message": "所有打分数据已清空，队伍和选手信息保留不变。"})

# ─────────────────────────────────────────────── Frontend pages ──

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>决赛打分系统 · 中兴捧月</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f6fa;color:#1a1a2e;min-height:100vh;}
.app{max-width:1100px;margin:0 auto;padding:0 16px 40px;}
.header{background:#1a1a2e;color:#fff;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;}
.header h1{font-size:18px;font-weight:600;letter-spacing:.5px;}
.header .user-info{font-size:13px;opacity:.8;display:flex;align-items:center;gap:12px;}
.btn-logout{background:rgba(255,255,255,.15);border:none;color:#fff;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px;}
.btn-logout:hover{background:rgba(255,255,255,.25);}
.login-page{display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);}
.login-card{background:#fff;border-radius:16px;padding:40px;width:380px;box-shadow:0 20px 60px rgba(0,0,0,.3);}
.login-card h2{font-size:22px;font-weight:700;margin-bottom:6px;color:#1a1a2e;}
.login-card p{font-size:13px;color:#888;margin-bottom:28px;}
.login-tabs{display:flex;gap:0;margin-bottom:24px;border:1.5px solid #e0e0e0;border-radius:8px;overflow:hidden;}
.login-tab{flex:1;padding:8px;text-align:center;font-size:13px;font-weight:500;cursor:pointer;background:#f8f9fa;color:#888;transition:all .2s;}
.login-tab.active{background:#1a1a2e;color:#fff;}
.form-group{margin-bottom:18px;}
.form-group label{display:block;font-size:13px;font-weight:500;color:#444;margin-bottom:6px;}
.form-group input,.form-group select{width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:14px;outline:none;transition:border-color .2s;}
.form-group input:focus,.form-group select:focus{border-color:#1a1a2e;}
.btn-primary{width:100%;padding:12px;background:#1a1a2e;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;transition:background .2s;}
.btn-primary:hover{background:#16213e;}
.error-msg{color:#e53e3e;font-size:13px;margin-top:10px;display:none;}
.tabs{display:flex;gap:4px;margin:20px 0 0;border-bottom:2px solid #e8e8e8;}
.tab{padding:10px 20px;cursor:pointer;font-size:14px;font-weight:500;color:#888;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .2s;}
.tab.active{color:#1a1a2e;border-bottom-color:#1a1a2e;}
.tab.hidden{display:none;}
.tab-content{display:none;padding:20px 0;}
.tab-content.active{display:block;}
.team-tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;}
.team-btn{padding:6px 14px;border:1.5px solid #e0e0e0;border-radius:8px;cursor:pointer;font-size:13px;background:#fff;transition:all .2s;}
.team-btn:hover,.team-btn.active{background:#1a1a2e;color:#fff;border-color:#1a1a2e;}
.score-table{width:100%;border-collapse:collapse;font-size:13px;}
.score-table th{background:#1a1a2e;color:#fff;padding:10px 12px;text-align:left;font-weight:500;white-space:nowrap;}
.score-table td{padding:10px 12px;border-bottom:1px solid #e8e8e8;vertical-align:middle;}
.score-table tr:nth-child(even) td{background:#f8f9fa;}
.score-table tr:hover td{background:#eef2ff;}
.medal-1::before{content:"🥇 ";}
.medal-2::before{content:"🥈 ";}
.medal-3::before{content:"🥉 ";}
.student-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;}
.student-card{background:#fff;border-radius:12px;padding:20px;border:1.5px solid #e8e8e8;}
.student-card.reviewed{border-color:#48bb78;}
.student-card .s-header{display:flex;align-items:center;gap:10px;margin-bottom:14px;}
.s-avatar{width:40px;height:40px;border-radius:50%;background:#e8f0fe;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;color:#1a1a2e;flex-shrink:0;}
.s-name{font-size:14px;font-weight:600;}
.s-role{font-size:12px;color:#888;}
.star-rating{display:flex;gap:4px;margin-bottom:4px;}
.star{font-size:22px;cursor:pointer;color:#ddd;transition:color .15s;}
.star.active,.star:hover{color:#f6ad55;}
.field-label{font-size:12px;font-weight:500;color:#666;margin-bottom:4px;margin-top:10px;}
.field-textarea{width:100%;padding:8px 10px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:13px;resize:vertical;min-height:52px;font-family:inherit;outline:none;}
.field-textarea:focus{border-color:#1a1a2e;}
.char-count{font-size:11px;color:#aaa;text-align:right;}
.recommend-row{display:flex;align-items:center;justify-content:space-between;margin-top:10px;padding:8px 10px;background:#f8f9fa;border-radius:8px;}
.recommend-row label{font-size:13px;font-weight:500;}
.toggle{position:relative;display:inline-block;width:44px;height:24px;}
.toggle input{opacity:0;width:0;height:0;}
.slider{position:absolute;cursor:pointer;inset:0;background:#e0e0e0;border-radius:12px;transition:.3s;}
.slider:before{position:absolute;content:"";height:18px;width:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s;}
input:checked+.slider{background:#48bb78;}
input:checked+.slider:before{transform:translateX(20px);}
.btn-save{margin-top:14px;width:100%;padding:10px;background:#1a1a2e;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;}
.btn-save:hover{background:#16213e;}
.btn-save.saved{background:#48bb78;}
.btn-export{padding:8px 18px;background:#48bb78;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;margin-right:8px;}
.btn-export:hover{background:#38a169;}
.btn-admin{padding:6px 14px;background:transparent;border:1.5px solid #1a1a2e;color:#1a1a2e;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;}
.btn-admin:hover{background:#1a1a2e;color:#fff;}
.btn-sm{padding:4px 10px;font-size:12px;border-radius:6px;cursor:pointer;border:1px solid #e0e0e0;background:#fff;color:#333;}
.btn-sm:hover{background:#f0f0f0;}
.btn-sm.danger{border-color:#e53e3e;color:#e53e3e;}
.btn-sm.danger:hover{background:#fff5f5;}
.btn-sm.primary{background:#1a1a2e;color:#fff;border-color:#1a1a2e;}
.btn-sm.primary:hover{background:#16213e;}
.progress-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
.pb-done{background:#c6f6d5;color:#276749;}
.pb-pending{background:#fed7d7;color:#9b2c2c;}
.toast{position:fixed;bottom:24px;right:24px;background:#1a1a2e;color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;z-index:9999;opacity:0;transition:opacity .3s;pointer-events:none;}
.toast.show{opacity:1;}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:500;display:flex;align-items:center;justify-content:center;}
.modal{background:#fff;border-radius:16px;padding:28px;width:440px;max-width:95vw;max-height:90vh;overflow-y:auto;}
.modal h3{font-size:16px;font-weight:700;margin-bottom:16px;}
.modal .btn-row{display:flex;gap:10px;margin-top:20px;}
.modal .btn-row button{flex:1;padding:10px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;}
.btn-cancel{background:#f0f0f0;border:none;color:#333;}
.btn-confirm{background:#1a1a2e;border:none;color:#fff;}
.info-bar{background:#e8f0fe;border-radius:10px;padding:12px 16px;font-size:13px;color:#2c5282;margin-bottom:16px;}
.warn-bar{background:#fff3cd;border-radius:10px;padding:12px 16px;font-size:13px;color:#856404;margin-bottom:16px;}
.admin-section{margin-bottom:32px;}
.admin-section h2{font-size:16px;font-weight:700;margin-bottom:14px;color:#1a1a2e;padding-bottom:8px;border-bottom:2px solid #e8e8e8;display:flex;align-items:center;gap:10px;}
.section-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px;}
.section-toolbar h3{flex:1;font-size:15px;font-weight:600;}
.admin-sub-tabs{display:flex;gap:4px;margin-bottom:20px;border-bottom:1.5px solid #e8e8e8;}
.admin-sub-tab{padding:8px 16px;cursor:pointer;font-size:13px;font-weight:500;color:#888;border-bottom:2px solid transparent;margin-bottom:-1.5px;transition:all .2s;}
.admin-sub-tab.active{color:#1a1a2e;border-bottom-color:#1a1a2e;}
.admin-panel{display:none;}
.admin-panel.active{display:block;}
.edit-input{padding:5px 8px;border:1.5px solid #e0e0e0;border-radius:6px;font-size:13px;outline:none;flex:1;}
.edit-input:focus{border-color:#1a1a2e;}
.tag-admin{display:inline-block;padding:2px 8px;background:#e8f0fe;color:#185FA5;border-radius:4px;font-size:11px;font-weight:600;}
/* 打分表格 */
.score-grid{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px;}
.score-grid th{background:#f0f4ff;padding:8px 10px;text-align:center;font-weight:600;border:1px solid #e0e0e0;white-space:nowrap;}
.score-grid td{padding:6px 8px;border:1px solid #e0e0e0;text-align:center;vertical-align:middle;}
.score-grid td input{width:60px;padding:4px 6px;border:1.5px solid #e0e0e0;border-radius:6px;font-size:13px;text-align:center;outline:none;}
.score-grid td input:focus{border-color:#1a1a2e;}
.score-grid td.team-name{text-align:left;font-weight:500;white-space:nowrap;}
.score-grid td.saved-score{background:#c6f6d5;}
/* 权重矩阵 */
.weight-matrix{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px;}
.weight-matrix th{background:#f0f4ff;padding:8px 10px;text-align:center;font-weight:600;border:1px solid #e0e0e0;white-space:nowrap;position:sticky;top:0;}
.weight-matrix td{padding:6px 8px;border:1px solid #e0e0e0;text-align:center;vertical-align:middle;}
.weight-matrix td input{width:70px;padding:4px 6px;border:1.5px solid #e0e0e0;border-radius:6px;font-size:13px;text-align:center;outline:none;}
.weight-matrix td input:focus{border-color:#1a1a2e;}
.weight-matrix td.judge-name{text-align:left;font-weight:500;white-space:nowrap;}
.weight-row-total{background:#f8f9fa;font-weight:600;}
/* 工作地单选 */
.work-location-group{display:flex;gap:12px;margin-top:4px;}
.work-location-group label{font-size:13px;cursor:pointer;display:flex;align-items:center;gap:4px;}
/* 星级计数 */
.star-counter{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:#fff3cd;border-radius:8px;font-size:13px;color:#856404;font-weight:500;}
.star-counter.full{background:#fed7d7;color:#9b2c2c;}
</style>
</head>
<body>

<!-- Login Page -->
<div id="login-page" class="login-page" style="display:none;">
  <div class="login-card">
    <h2>决赛打分系统</h2>
    <p>中兴捧月营销大赛决赛</p>
    <div class="login-tabs">
      <div class="login-tab active" id="lt-examiner" onclick="switchLoginTab('examiner')">评委登录</div>
      <div class="login-tab" id="lt-admin" onclick="switchLoginTab('admin')">管理员登录</div>
    </div>
    <div id="lp-examiner">
      <div class="form-group">
        <label>用户名（姓名）</label>
        <input type="text" id="login-username" placeholder="请输入您的姓名" autocomplete="off">
      </div>
      <div class="form-group">
        <label>密码（工号）</label>
        <input type="password" id="login-pwd" placeholder="请输入您的工号" autocomplete="off">
      </div>
      <button class="btn-primary" onclick="doLogin()">评委登录</button>
      <div class="error-msg" id="login-err"></div>
    </div>
    <div id="lp-admin" style="display:none;">
      <div class="warn-bar">管理员模式仅用于查看汇总结果、编辑数据及导出，无法以评委身份打分。</div>
      <div class="form-group">
        <label>用户名</label>
        <input type="text" id="admin-name-login" placeholder="请输入管理员用户名" autocomplete="off">
      </div>
      <div class="form-group">
        <label>密码</label>
        <input type="password" id="admin-pwd-login" placeholder="请输入管理员密码" autocomplete="off">
      </div>
      <button class="btn-primary" onclick="doAdminOnlyLogin()">管理员登录</button>
      <div class="error-msg" id="admin-login-err"></div>
    </div>
  </div>
</div>

<!-- Main App -->
<div id="main-app" style="display:none;">
  <div class="header">
    <h1>决赛打分系统</h1>
    <div class="user-info">
      <span id="header-username"></span>
      <span id="header-role" style="font-size:11px;opacity:.6;"></span>
      <span id="header-admin-badge" class="tag-admin" style="display:none;">管理员</span>
      <button id="btn-enter-admin" class="btn-logout" onclick="showUpgradeAdminModal()" style="display:none;">升级管理员</button>
      <button class="btn-logout" onclick="doLogout()">退出</button>
    </div>
  </div>
  <div class="app">
    <div class="tabs" id="main-tabs">
      <div class="tab active" id="tab-btn-scoring" onclick="switchTab('scoring')">团队打分</div>
      <div class="tab" id="tab-btn-reviews" onclick="switchTab('reviews')">个人点评</div>
      <div class="tab hidden" id="tab-btn-admin" onclick="switchTab('admin')">管理后台</div>
    </div>

    <!-- Scoring Tab -->
    <div id="tab-scoring" class="tab-content active">
      <div class="info-bar">请为每个环节的每支队伍打分，点击"保存"按钮提交。分数可随时修改。</div>
      <div id="scoring-grid-container"></div>
    </div>

    <!-- Reviews Tab -->
    <div id="tab-reviews" class="tab-content">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <div class="info-bar" style="margin-bottom:0;flex:1;">选择队伍后，为该队伍的每位选手填写点评和评分。</div>
        <div class="star-counter" id="star-counter">已推荐 0 / 6</div>
      </div>
      <div class="team-tabs" id="review-team-tabs"></div>
      <div class="student-cards" id="student-cards"></div>
    </div>

    <!-- Admin Tab -->
    <div id="tab-admin" class="tab-content">
      <div class="admin-sub-tabs">
        <div class="admin-sub-tab active" onclick="switchAdminPanel('ranking')">排名看板</div>
        <div class="admin-sub-tab" onclick="switchAdminPanel('examiners')">评委管理</div>
        <div class="admin-sub-tab" onclick="switchAdminPanel('weights')">权重矩阵</div>
        <div class="admin-sub-tab" onclick="switchAdminPanel('stages')">环节分数配置</div>
        <div class="admin-sub-tab" onclick="switchAdminPanel('progress')">提交进度</div>
      </div>

      <!-- Ranking Panel -->
      <div id="panel-ranking" class="admin-panel active">
        <div class="admin-section">
          <h2>团队排名 &amp; 总分</h2>
          <div style="overflow-x:auto;">
            <table class="score-table" id="team-score-table">
              <thead id="team-score-head"></thead>
              <tbody id="team-score-body"></tbody>
            </table>
          </div>
        </div>
        <div class="admin-section">
          <h2>营销之星 Top 10</h2>
          <div style="overflow-x:auto;">
            <table class="score-table">
              <thead><tr><th>排名</th><th>姓名</th><th>队伍</th><th>角色</th><th>平均分</th><th>推荐票</th><th>评委数</th></tr></thead>
              <tbody id="star-score-body"></tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Examiners Panel -->
      <div id="panel-examiners" class="admin-panel">
        <div class="admin-section">
          <h2>评委管理 <button class="btn-sm primary" style="margin-left:auto;" onclick="showAddExaminerModal()">+ 新增评委</button></h2>
          <div style="overflow-x:auto;">
            <table class="score-table">
              <thead><tr><th>ID</th><th>用户名</th><th>姓名</th><th>权限角色</th><th>角色显示名称</th><th>登录密码</th><th>状态</th><th>操作</th></tr></thead>
              <tbody id="examiners-edit-body"></tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Weights Panel -->
      <div id="panel-weights" class="admin-panel">
        <div class="admin-section">
          <h2>评委打分权重矩阵</h2>
          <div class="info-bar">设置每位评委在各环节的权重（0~1 小数）。留空表示该评委不参与该环节打分。每列权重之和应等于 1，系统会提示偏差但允许保存。示例：3 位评委等权则各填 0.3333。</div>
          <div style="overflow-x:auto;" id="weight-matrix-container"></div>
          <div style="margin-top:16px;display:flex;align-items:center;gap:16px;">
            <button class="btn-primary" style="max-width:140px;" onclick="saveWeightsMatrix()">保存权重</button>
            <span id="weights-hint" style="font-size:13px;color:#888;"></span>
          </div>
          <div class="error-msg" id="weights-err" style="margin-top:8px;"></div>
        </div>
      </div>

      <!-- Stages Panel -->
      <div id="panel-stages" class="admin-panel">
        <div class="admin-section">
          <h2>环节分数配置</h2>
          <div class="info-bar">调整各环节的满分分值。修改后新分数立即生效，历史已打分数不变。</div>
          <div id="stages-config-container"></div>
        </div>
      </div>

      <!-- Progress Panel -->
      <div id="panel-progress" class="admin-panel">
        <div class="admin-section">
          <h2>评委提交进度</h2>
          <div style="overflow-x:auto;">
            <table class="score-table" id="progress-table">
              <thead id="progress-head"></thead>
              <tbody id="progress-body"></tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Export (hidden panel, triggered by button) -->
      <div style="margin-top:24px;padding:20px;background:#fff;border-radius:12px;border:1.5px solid #e8e8e8;">
        <h3 style="font-size:15px;font-weight:600;margin-bottom:12px;">导出数据</h3>
        <div class="info-bar">导出的 CSV 文件可用 Excel 直接打开，中文显示正常。</div>
        <button class="btn-export" onclick="exportData('scores')">导出团队打分 CSV</button>
        <button class="btn-export" onclick="exportData('reviews')">导出个人点评 CSV</button>
        <div style="margin-top:24px;border-top:1px solid #eee;padding-top:20px;">
          <h3 style="font-size:15px;font-weight:600;margin-bottom:12px;color:#c53030;">⚠️ 危险操作</h3>
          <button class="btn-export" style="background:#e53e3e;" onclick="clearScoresConfirm()">🗑️ 清空所有打分数据</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- 升级管理员弹窗 -->
<div class="modal-overlay" id="upgrade-admin-modal" style="display:none;">
  <div class="modal">
    <h3>升级为管理员</h3>
    <p style="font-size:13px;color:#666;margin-bottom:16px;">输入管理员用户名和密码后，您将同时拥有评委打分和后台管理权限。</p>
    <div class="form-group"><label>用户名</label><input type="text" id="upgrade-admin-name" placeholder="请输入管理员用户名"></div>
    <div class="form-group"><label>密码</label><input type="password" id="upgrade-admin-pwd" placeholder="请输入管理员密码"></div>
    <div class="error-msg" id="upgrade-admin-err"></div>
    <div class="btn-row">
      <button class="btn-cancel" onclick="closeModal('upgrade-admin-modal')">取消</button>
      <button class="btn-confirm" onclick="doUpgradeAdmin()">确认</button>
    </div>
  </div>
</div>

<!-- 新增评委弹窗 -->
<div class="modal-overlay" id="add-examiner-modal" style="display:none;">
  <div class="modal">
    <h3>新增评委</h3>
    <div class="form-group"><label>姓名</label><input type="text" id="ne-name" placeholder="评委姓名"></div>
    <div class="form-group">
      <label>权限角色</label>
      <select id="ne-role">
        <option value="cxo">CXO</option>
        <option value="expert">评审专家</option>
        <option value="observer">观察员</option>
      </select>
    </div>
    <div class="form-group"><label>角色显示名称</label><input type="text" id="ne-role-display" placeholder="如：CEO秘书、技术顾问（选填）"></div>
    <div class="form-group"><label>登录密码</label><input type="text" id="ne-pwd" placeholder="唯一密码，评委用此登录"></div>
    <div class="error-msg" id="ne-err"></div>
    <div class="btn-row">
      <button class="btn-cancel" onclick="closeModal('add-examiner-modal')">取消</button>
      <button class="btn-confirm" onclick="doAddExaminer()">添加</button>
    </div>
  </div>
</div>

<!-- 重置密码弹窗 -->
<div class="modal-overlay" id="reset-pwd-modal" style="display:none;">
  <div class="modal">
    <h3>重置密码</h3>
    <p style="font-size:13px;color:#666;margin-bottom:16px;">重置后该评委需要使用新密码登录，旧登录将自动失效。</p>
    <div class="form-group"><label>新密码</label><input type="text" id="rp-pwd" placeholder="请输入新密码"></div>
    <div class="error-msg" id="rp-err"></div>
    <div class="btn-row">
      <button class="btn-cancel" onclick="closeModal('reset-pwd-modal')">取消</button>
      <button class="btn-confirm" onclick="doResetPwd()">确认重置</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let allTeams=[], allStudents=[], myScores={}, myReviews={}, allStages=[];
let currentReviewTeam=null, isObserver=false;
let isAdminMode=false, isExaminerMode=false;
let _dirty = false;  // 未保存标记
let _resetPwdId = null;

async function api(path,method='GET',body=null){
  const opts={method,headers:{'Content-Type':'application/json'}};
  if(body)opts.body=JSON.stringify(body);
  const r=await fetch(path,opts);
  const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(j.error||'请求失败('+r.status+')');
  return j;
}

function toast(msg,dur=2500){
  const t=document.getElementById('toast');
  t.textContent=msg;t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),dur);
}

function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

// ─── beforeunload
window.addEventListener('beforeunload', function(e){
  if(_dirty){
    e.preventDefault();
    e.returnValue='当前页面有未保存的评分，是否确定离开？';
  }
});

async function init(){
  try{
    const me=await api('/api/me');
    if(me.examiner_id && me.examiner_id!=='__admin__'){
      showApp(me);
    } else if(me.is_admin){
      showApp(me);
    } else {
      showLogin();
    }
  }catch{showLogin();}
}

function showLogin(){
  document.getElementById('login-page').style.display='flex';
  document.getElementById('main-app').style.display='none';
}

function switchLoginTab(tab){
  document.getElementById('lt-examiner').classList.toggle('active',tab==='examiner');
  document.getElementById('lt-admin').classList.toggle('active',tab==='admin');
  document.getElementById('lp-examiner').style.display=tab==='examiner'?'':'none';
  document.getElementById('lp-admin').style.display=tab==='admin'?'':'none';
}

function showApp(me){
  document.getElementById('login-page').style.display='none';
  document.getElementById('main-app').style.display='block';
  isAdminMode=me.is_admin;
  isExaminerMode=me.examiner_id && me.examiner_id!=='__admin__';
  isObserver=me.is_observer||false;

  document.getElementById('header-username').textContent=me.examiner_name||'';
  const roleMap={cxo:'CXO',expert:'评审专家',observer:'观察员'};
  document.getElementById('header-role').textContent=me.role_type?roleMap[me.role_type]||'':'';
  document.getElementById('header-admin-badge').style.display=me.is_admin?'':'none';

  // 团队打分Tab：观察员隐藏
  const scoringTab=document.getElementById('tab-btn-scoring');
  const reviewTab=document.getElementById('tab-btn-reviews');
  if(isObserver){
    scoringTab.classList.add('hidden');
  } else {
    scoringTab.classList.remove('hidden');
  }

  if(!isExaminerMode && isAdminMode){
    scoringTab.classList.add('hidden');
    reviewTab.classList.add('hidden');
  }

  if(isAdminMode){
    document.getElementById('tab-btn-admin').classList.remove('hidden');
  } else {
    document.getElementById('btn-enter-admin').style.display='';
  }

  if(!isExaminerMode && isAdminMode){
    switchTab('admin');
  } else {
    loadExaminerData();
    // 默认第一个可见tab
    if(isObserver){
      switchTab('reviews');
    } else {
      switchTab('scoring');
    }
  }
}

async function doLogin(){
  const username=document.getElementById('login-username').value.trim();
  const pwd=document.getElementById('login-pwd').value.trim();
  const err=document.getElementById('login-err');
  err.style.display='none';
  if(!username){err.textContent='请输入用户名';err.style.display='block';return;}
  try{
    await api('/api/login','POST',{username,password:pwd});
    const meInfo=await api('/api/me');
    showApp(meInfo);
  }catch(e){
    err.textContent=e.message;err.style.display='block';
  }
}

async function doAdminOnlyLogin(){
  const username=document.getElementById('admin-name-login').value.trim();
  const pwd=document.getElementById('admin-pwd-login').value.trim();
  const err=document.getElementById('admin-login-err');
  err.style.display='none';
  if(!username){err.textContent='请输入用户名';err.style.display='block';return;}
  try{
    await api('/api/admin_login','POST',{username,password:pwd});
    const meInfo=await api('/api/me');
    showApp(meInfo);
  }catch(e){err.textContent=e.message;err.style.display='block';}
}

document.addEventListener('keydown',e=>{
  if(e.key==='Enter'){
    const lp=document.getElementById('login-page');
    if(lp.style.display!=='none'){
      const adminTab=document.getElementById('lt-admin').classList.contains('active');
      if(adminTab) doAdminOnlyLogin(); else doLogin();
    }
  }
});

async function doLogout(){
  await api('/api/logout','POST');
  location.reload();
}

function showUpgradeAdminModal(){
  document.getElementById('upgrade-admin-modal').style.display='flex';
}
function closeUpgradeAdminModal(){
  document.getElementById('upgrade-admin-modal').style.display='none';
}
async function doUpgradeAdmin(){
  const username=document.getElementById('upgrade-admin-name').value.trim();
  const pwd=document.getElementById('upgrade-admin-pwd').value;
  const err=document.getElementById('upgrade-admin-err');
  err.style.display='none';
  if(!username){err.textContent='请输入用户名';err.style.display='block';return;}
  try{
    await api('/api/admin_login','POST',{username,password:pwd});
    closeUpgradeAdminModal();
    toast('已升级为管理员 ✓');
    isAdminMode=true;
    document.getElementById('tab-btn-admin').classList.remove('hidden');
    document.getElementById('header-admin-badge').style.display='';
    document.getElementById('btn-enter-admin').style.display='none';
    switchTab('admin');
  }catch(e){err.textContent=e.message;err.style.display='block';}
}

function switchTab(name){
  ['scoring','reviews','admin'].forEach(t=>{
    const btn=document.getElementById('tab-btn-'+t);
    if(btn)btn.classList.toggle('active',t===name);
  });
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  if(name==='admin')loadAdmin();
}

async function loadExaminerData(){
  try{
    const[teams,students,stages,scores,reviews,starCount]=await Promise.all([
      api('/api/teams'),
      api('/api/students'),
      api('/api/stages'),
      api('/api/scores'),
      api('/api/reviews'),
      api('/api/reviews/star_count')
    ]);
    allTeams=teams;
    allStudents=students;
    allStages=stages;
    myScores=scores;
    myReviews=reviews;
    updateStarCounter(starCount.count);
    renderScoringGrid();
    renderTeamTabs();
  }catch(e){toast('加载数据失败：'+e.message);}
}

// ─── Scoring Grid (新模型：表格输入)
function renderScoringGrid(){
  const container=document.getElementById('scoring-grid-container');
  if(!allStages.length||!allTeams.length){
    container.innerHTML='<div class="info-bar">暂无环节或队伍数据</div>';
    return;
  }
  // 每个环节一个表格
  let html='';
  for(const stage of allStages){
    html+=`<div style="margin-bottom:24px;">
      <h3 style="font-size:14px;font-weight:600;margin-bottom:8px;">${escHtml(stage.name)} <span style="font-weight:400;color:#888;">（满分 ${stage.max_score}）</span></h3>
      <table class="score-grid">
        <thead><tr><th style="width:160px;text-align:left;">队伍</th><th style="width:80px;">分数</th><th style="width:60px;">状态</th></tr></thead>
        <tbody>`;
    for(const team of allTeams){
      const existing=myScores[String(stage.id)]?.[String(team.id)];
      const val=existing!==undefined?existing:'';
      const saved=existing!==undefined?'saved-score':'';
      html+=`<tr>
        <td class="team-name">${escHtml(team.name)} (${escHtml(team.code)}队)</td>
        <td class="${saved}"><input type="number" id="score-${stage.id}-${team.id}" value="${val}" min="0" max="${stage.max_score}" step="0.5" placeholder="0~${stage.max_score}" onchange="markDirty()"></td>
        <td class="${saved}">${existing!==undefined?'<span style="color:#48bb78;font-size:11px;">已保存</span>':'<span style="color:#aaa;font-size:11px;">—</span>'}</td>
      </tr>`;
    }
    html+=`</tbody></table>
      <div style="margin-top:8px;">
        <button class="btn-primary" style="max-width:120px;padding:8px 16px;font-size:13px;" onclick="saveStageScores(${stage.id})">保存本环节</button>
      </div>
    </div>`;
  }
  container.innerHTML=html;
}

async function saveStageScores(stageId){
  const data=[];
  for(const team of allTeams){
    const el=document.getElementById(`score-${stageId}-${team.id}`);
    if(!el)continue;
    const val=el.value.trim();
    if(val==='')continue;
    const score=parseFloat(val);
    const stage=allStages.find(s=>s.id===stageId);
    if(isNaN(score)||score<0||score>(stage?.max_score||999)){
      toast(`分数必须在0~${stage?.max_score||0}之间`);
      return;
    }
    data.push({team_id:team.id, stage_id:stageId, score});
  }
  if(!data.length){toast('没有需要保存的分数');return;}
  try{
    await api('/api/scores/batch','POST',data);
    toast('分数已保存 ✓');
    _dirty=false;
    // 刷新本地缓存
    myScores=await api('/api/scores');
    renderScoringGrid();
  }catch(e){toast('保存失败：'+e.message);}
}

function markDirty(){_dirty=true;}

// ─── Reviews
function updateStarCounter(count){
  const el=document.getElementById('star-counter');
  el.textContent=`已推荐 ${count} / 6`;
  el.classList.toggle('full', count>=6);
}

function renderTeamTabs(){
  const tabs=document.getElementById('review-team-tabs');
  tabs.innerHTML=allTeams.map(t=>`
    <button class="team-btn ${currentReviewTeam===t.id?'active':''}" onclick="selectReviewTeam(${t.id})">${escHtml(t.name)}</button>
  `).join('');
  if(!currentReviewTeam&&allTeams.length)selectReviewTeam(allTeams[0].id);
}

function selectReviewTeam(tid){
  currentReviewTeam=tid;
  document.querySelectorAll('.team-btn').forEach(b=>b.classList.remove('active'));
  event?.target?.classList.add('active');
  renderStudentCards(tid);
}

function renderStudentCards(tid){
  const students=allStudents.filter(s=>s.team_id===tid);
  const container=document.getElementById('student-cards');
  container.innerHTML=students.map(s=>{
    const rev=myReviews[s.id]||{};
    const score=rev.overall_score||0;
    const stars=[1,2,3,4,5].map(v=>`<span class="star ${v<=score?'active':''}" onclick="setStar(${s.id},${v})">★</span>`).join('');
    const reviewed=myReviews[s.id]?'reviewed':'';
    const wl=rev.work_location||'';
    const isStar=rev.is_marketing_star?true:false;
    return `<div class="student-card ${reviewed}" id="scard-${s.id}">
      <div class="s-header">
        <div class="s-avatar">${s.name.slice(-1)}</div>
        <div><div class="s-name">${escHtml(s.name)}</div><div class="s-role">${escHtml(s.role)}</div></div>
        ${myReviews[s.id]?'<span style="font-size:11px;color:#48bb78;margin-left:auto;">已保存</span>':''}
      </div>
      <div class="field-label">整体评估 (1-5分)</div>
      <div class="star-rating" id="stars-${s.id}">${stars}</div>
      <div class="field-label">比赛中的亮点</div>
      <textarea class="field-textarea" id="highlight-${s.id}" maxlength="500" placeholder="请描述该选手的亮点..." oninput="updateCharCount(this,'cc-h-${s.id}')">${rev.highlight||''}</textarea>
      <div class="char-count" id="cc-h-${s.id}">${(rev.highlight||'').length}/500</div>
      <div class="field-label">比赛中的不足</div>
      <textarea class="field-textarea" id="weakness-${s.id}" maxlength="500" placeholder="请描述需要改进的地方..." oninput="updateCharCount(this,'cc-w-${s.id}')">${rev.weakness||''}</textarea>
      <div class="char-count" id="cc-w-${s.id}">${(rev.weakness||'').length}/500</div>
      <div class="field-label">意向签约评估</div>
      <textarea class="field-textarea" id="intent-${s.id}" maxlength="500" placeholder="意向签约评价..." oninput="updateCharCount(this,'cc-i-${s.id}')">${rev.intent||''}</textarea>
      <div class="char-count" id="cc-i-${s.id}">${(rev.intent||'').length}/500</div>
      <div class="field-label">推荐工作地</div>
      <div class="work-location-group">
        <label><input type="radio" name="wl-${s.id}" value="国内" ${wl==='国内'?'checked':''}> 国内</label>
        <label><input type="radio" name="wl-${s.id}" value="海外" ${wl==='海外'?'checked':''}> 海外</label>
      </div>
      <div class="recommend-row">
        <label>推荐为"营销之星"</label>
        <label class="toggle"><input type="checkbox" id="rec-${s.id}" ${isStar?'checked':''} onchange="checkStarLimit(${s.id})"><span class="slider"></span></label>
      </div>
      <button class="btn-save" id="save-btn-${s.id}" onclick="saveReview(${s.id})">保存点评</button>
    </div>`;
  }).join('');
}

function setStar(sid,val){
  (myReviews[sid]=myReviews[sid]||{}).overall_score=val;
  [...document.getElementById('stars-'+sid).querySelectorAll('.star')].forEach((s,i)=>s.classList.toggle('active',i<val));
  markDirty();
}

function updateCharCount(el,countId){
  document.getElementById(countId).textContent=el.value.length+'/500';
}

async function checkStarLimit(sid){
  const cb=document.getElementById('rec-'+sid);
  if(!cb.checked) return; // 取消勾选不需要检查
  try{
    const info=await api('/api/reviews/star_count');
    // 如果当前选手未被推荐，检查是否超限
    const currentRev=myReviews[sid];
    const alreadyStar=currentRev?.is_marketing_star?true:false;
    if(!alreadyStar && info.count>=6){
      cb.checked=false;
      toast('每位评委最多推荐6名营销之星');
    }
  }catch(e){}
}

async function saveReview(sid){
  const score=myReviews[sid]?.overall_score||0;
  if(!score){toast('请先选择整体评分');return;}
  const data={
    student_id:sid,
    overall_score:score,
    highlight:document.getElementById('highlight-'+sid).value,
    weakness:document.getElementById('weakness-'+sid).value,
    intent:document.getElementById('intent-'+sid).value,
    work_location:document.querySelector(`input[name="wl-${sid}"]:checked`)?.value||'',
    is_marketing_star:document.getElementById('rec-'+sid).checked
  };
  try{
    await api('/api/reviews','POST',data);
    myReviews[sid]=data;
    const btn=document.getElementById('save-btn-'+sid);
    btn.textContent='已保存 ✓';btn.classList.add('saved');
    document.getElementById('scard-'+sid).classList.add('reviewed');
    toast('点评已保存 ✓');
    _dirty=false;
    // 更新推荐计数
    const info=await api('/api/reviews/star_count');
    updateStarCounter(info.count);
    setTimeout(()=>{btn.textContent='保存点评';btn.classList.remove('saved');},2000);
  }catch(e){toast('保存失败：'+e.message);}
}

// ─── Admin
let currentAdminPanel='ranking';
function switchAdminPanel(name){
  const panels=['ranking','examiners','weights','stages','progress'];
  document.querySelectorAll('.admin-sub-tab').forEach((t,i)=>{
    t.classList.toggle('active',panels[i]===name);
  });
  document.querySelectorAll('.admin-panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  currentAdminPanel=name;
  if(name==='ranking')loadAdminRanking();
  if(name==='examiners')loadExaminers();
  if(name==='weights')loadWeightsMatrix();
  if(name==='stages')loadStagesConfig();
  if(name==='progress')loadProgress();
}

async function loadAdmin(){
  loadAdminRanking();
}

async function loadAdminRanking(){
  try{
    const[scoresData,stars]=await Promise.all([
      api('/api/scores/teams'),
      api('/api/scores/stars')
    ]);
    renderTeamScores(scoresData);
    renderStarScores(stars);
  }catch(e){toast('加载分数失败：'+e.message);}
}

function renderTeamScores(scoresData){
  const teams=scoresData.teams;
  const stages=scoresData.stages;
  const headCols=stages.map(s=>`<th>${escHtml(s.name)}<br><span style="font-weight:400;font-size:11px;">(满分${s.max_score})</span></th>`).join('');
  document.getElementById('team-score-head').innerHTML=`<tr><th>名次</th><th>队伍</th><th>总分(百分制)</th>${headCols}</tr>`;
  const medals=['medal-1','medal-2','medal-3'];
  document.getElementById('team-score-body').innerHTML=teams.map((s,i)=>{
    const stageCols=stages.map(st=>`<td>${(s['stage_'+st.id+'_pct']||0).toFixed(2)}</td>`).join('');
    return `<tr>
      <td class="${medals[i]||''}">${s.rank}</td>
      <td><b>${escHtml(s.name)}</b> (${escHtml(s.code)}队)</td>
      <td><b>${s.total_score.toFixed(2)}</b></td>
      ${stageCols}
    </tr>`;
  }).join('');
}

function renderStarScores(stars){
  const medals=['🥇','🥈','🥉'];
  document.getElementById('star-score-body').innerHTML=stars.map((s,i)=>`
    <tr>
      <td>${i<3?medals[i]:s.star_rank}</td>
      <td><b>${escHtml(s.name)}</b></td><td>${escHtml(s.team_name)}</td><td>${escHtml(s.role)}</td>
      <td>${s.avg_score.toFixed(2)}</td><td>${s.recommend_count}</td>
      <td>${s.review_count}</td>
    </tr>`).join('');
}

// ─── Examiners Management
async function loadExaminers(){
  try{
    const examiners=await api('/api/admin/examiners');
    const roleMap={cxo:'CXO',expert:'评审专家',observer:'观察员'};
    document.getElementById('examiners-edit-body').innerHTML=examiners.map(e=>`
      <tr id="examiner-row-${e.id}">
        <td style="color:#aaa;font-size:12px;">#${e.id}</td>
        <td><input class="edit-input" id="eusername-${e.id}" value="${escHtml(e.username||e.name)}" placeholder="登录用户名"></td>
        <td><input class="edit-input" id="ename-${e.id}" value="${escHtml(e.name)}"></td>
        <td>
          <select class="edit-input" id="erole-${e.id}">
            <option value="cxo" ${e.role_type==='cxo'?'selected':''}>CXO</option>
            <option value="expert" ${e.role_type==='expert'?'selected':''}>评审专家</option>
            <option value="observer" ${e.role_type==='observer'?'selected':''}>观察员</option>
          </select>
        </td>
        <td><input class="edit-input" id="eroledisplay-${e.id}" value="${escHtml(e.role_display||'')}" placeholder="如：CEO秘书、技术顾问"></td>
        <td><input class="edit-input" id="epwd-${e.id}" value="${escHtml(e.password)}" style="font-family:monospace;" placeholder="工号"></td>
        <td>${e.is_active?'<span style="color:#48bb78;">启用</span>':'<span style="color:#e53e3e;">禁用</span>'}</td>
        <td style="white-space:nowrap;">
          <button class="btn-sm primary" onclick="saveExaminer(${e.id})">保存</button>
          <button class="btn-sm" onclick="showResetPwdModal(${e.id})">重置密码</button>
          <button class="btn-sm ${e.is_active?'danger':'primary'}" onclick="toggleExaminer(${e.id},${e.is_active?0:1})">${e.is_active?'禁用':'启用'}</button>
          <button class="btn-sm danger" onclick="deleteExaminer(${e.id})" style="margin-left:2px;">删除</button>
        </td>
      </tr>`).join('');
  }catch(e){toast('加载评委失败：'+e.message);}
}

async function saveExaminer(eid){
  const username=document.getElementById('eusername-'+eid).value.trim();
  const name=document.getElementById('ename-'+eid).value.trim();
  const role_type=document.getElementById('erole-'+eid).value;
  const role_display=document.getElementById('eroledisplay-'+eid).value.trim();
  const password=document.getElementById('epwd-'+eid).value.trim();
  try{
    await api('/api/admin/examiners/'+eid,'PUT',{username,name,role_type,role_display,password});
    toast('评委信息已更新 ✓');
  }catch(e){toast('保存失败：'+e.message);}
}

async function toggleExaminer(eid,active){
  try{
    await api('/api/admin/examiners/'+eid,'PUT',{name:'_',role_type:'expert',is_active:active});
    // 重新读取真实名字
    const examiners=await api('/api/admin/examiners');
    const ex=examiners.find(e=>e.id===eid);
    if(ex) await api('/api/admin/examiners/'+eid,'PUT',{name:ex.name,role_type:ex.role_type,is_active:active});
    toast(active?'已启用':'已禁用');
    loadExaminers();
  }catch(e){toast('操作失败：'+e.message);}
}

async function deleteExaminer(eid){
  if(!confirm('确认删除该评委？其所有提交的打分和点评记录也会一并删除。'))return;
  try{
    await api('/api/admin/examiners/'+eid,'DELETE');
    document.getElementById('examiner-row-'+eid)?.remove();
    toast('已删除 ✓');
  }catch(e){toast('删除失败：'+e.message);}
}

function showResetPwdModal(eid){
  _resetPwdId=eid;
  document.getElementById('rp-pwd').value='';
  document.getElementById('rp-err').style.display='none';
  document.getElementById('reset-pwd-modal').style.display='flex';
}

async function doResetPwd(){
  const pwd=document.getElementById('rp-pwd').value.trim();
  const err=document.getElementById('rp-err');
  err.style.display='none';
  if(!pwd){err.textContent='请输入新密码';err.style.display='block';return;}
  try{
    await api('/api/admin/examiners/'+_resetPwdId+'/reset_password','POST',{password:pwd});
    toast('密码已重置 ✓');
    closeModal('reset-pwd-modal');
    loadExaminers();
  }catch(e){err.textContent=e.message;err.style.display='block';}
}

function showAddExaminerModal(){
  document.getElementById('ne-name').value='';
  document.getElementById('ne-role').value='expert';
  document.getElementById('ne-pwd').value='';
  document.getElementById('ne-err').style.display='none';
  document.getElementById('add-examiner-modal').style.display='flex';
}

async function doAddExaminer(){
  const name=document.getElementById('ne-name').value.trim();
  const role_type=document.getElementById('ne-role').value;
  const role_display=document.getElementById('ne-role-display').value.trim();
  const password=document.getElementById('ne-pwd').value.trim();
  const err=document.getElementById('ne-err');
  err.style.display='none';
  try{
    await api('/api/admin/examiners','POST',{name,role_type,role_display,password});
    toast('评委已添加 ✓');
    closeModal('add-examiner-modal');
    loadExaminers();
  }catch(e){err.textContent=e.message;err.style.display='block';}
}

// ─── Weights Matrix
async function loadWeightsMatrix(){
  try{
    const data=await api('/api/weights/matrix');
    renderWeightsMatrix(data);
  }catch(e){toast('加载权重失败：'+e.message);}
}

function renderWeightsMatrix(data){
  window._weightStages=null;
  const {matrix, examiners, stages}=data;
  const container=document.getElementById('weight-matrix-container');
  let html='<table class="weight-matrix"><thead><tr><th>评委</th><th>角色</th>';
  for(const st of stages){
    html+=`<th>${escHtml(st.name)}</th>`;
  }
  html+='</tr></thead><tbody>';
  for(const ex of examiners){
    const roleMap={cxo:'CXO',expert:'评审专家',observer:'观察员'};
    const displayRole=ex.role_display||roleMap[ex.role_type]||ex.role_type;
    html+=`<tr><td class="judge-name">${escHtml(ex.name)}</td><td>${escHtml(displayRole)}</td>`;
    for(const st of stages){
      const val=matrix[String(ex.id)]?.[String(st.id)]??'';
      html+=`<td><input type="number" id="wt-${ex.id}-${st.id}" value="${val}" min="0" max="1" step="0.0001" placeholder="留空=0" oninput="updateWeightHints()"></td>`;
    }
    html+='</tr>';
  }
  // 汇总行
  html+='<tr class="weight-row-total"><td colspan="2" style="text-align:right;">权重之和</td>';
  for(const st of stages){
    html+=`<td id="wt-total-${st.id}">—</td>`;
  }
  html+='</tr></tbody></table>';
  container.innerHTML=html;
  updateWeightHints();
}

function updateWeightHints(){
  if(!window._weightStages){
    const totalCells=document.querySelectorAll('#weight-matrix-container [id^="wt-total-"]');
    window._weightStages=[];
    totalCells.forEach(cell=>{
      const sid=cell.id.replace('wt-total-','');
      window._weightStages.push(sid);
    });
  }
  for(const sid of window._weightStages){
    let total=0;
    const wInputs=document.querySelectorAll(`#weight-matrix-container input[id$="-${sid}"]`);
    console.log(`环节${sid} 输入框数量:`, wInputs.length);
    wInputs.forEach(inp=>{
      const v=parseFloat(inp.value);
      if(!isNaN(v))total+=v;
    });
    const cell=document.getElementById('wt-total-'+sid);
    if(cell){
      cell.textContent=total.toFixed(4);
      cell.style.color=Math.abs(total-1)<0.0001?'#48bb78':(total>0?'#e53e3e':'#aaa');
    }
  }
}

async function saveWeightsMatrix(){
  const err=document.getElementById('weights-err');
  err.style.display='none';
  // 收集矩阵数据
  const matrix={};
  const inputs=document.querySelectorAll('.weight-matrix input[type="number"]');
  inputs.forEach(inp=>{
    const parts=inp.id.split('-'); // wt-{eid}-{sid}
    if(parts.length<3)return;
    const eid=parts[1];
    const sid=parts.slice(2).join('-');
    const val=inp.value.trim();
    if(!matrix[eid])matrix[eid]={};
    matrix[eid][sid]=val===''?null:parseFloat(val);
  });
  try{
    const result=await api('/api/weights/matrix','POST',{matrix});
    toast('权重已保存 ✓');
    if(result.warnings&&result.warnings.length){
      toast(result.warnings.join('；'),4000);
    }
    loadWeightsMatrix();
  }catch(e){err.textContent=e.message;err.style.display='block';}
}

// ─── Stages Config
async function loadStagesConfig(){
  try{
    const stages=await api('/api/stages');
    const container=document.getElementById('stages-config-container');
    container.innerHTML=stages.map(st=>`
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;padding:12px;background:#fff;border-radius:8px;border:1px solid #e8e8e8;">
        <span style="font-size:14px;font-weight:500;width:160px;">${escHtml(st.name)}</span>
        <div style="display:flex;align-items:center;gap:6px;">
          <label style="font-size:13px;color:#666;">满分：</label>
          <input type="number" id="stage-max-${st.id}" value="${st.max_score}" min="1" max="100" step="1"
            style="width:70px;padding:6px 10px;border:1.5px solid #e0e0e0;border-radius:6px;font-size:14px;text-align:center;">
          <span style="font-size:14px;color:#666;">分</span>
        </div>
        <button class="btn-sm primary" onclick="saveStage(${st.id})">保存</button>
      </div>
    `).join('');
  }catch(e){toast('加载环节配置失败：'+e.message);}
}

async function saveStage(stageId){
  const maxScore=parseFloat(document.getElementById('stage-max-'+stageId).value);
  if(isNaN(maxScore)||maxScore<=0){toast('满分必须大于0');return;}
  try{
    await api('/api/admin/stages/'+stageId,'PUT',{max_score:maxScore});
    toast('环节配置已保存 ✓');
  }catch(e){toast('保存失败：'+e.message);}
}

// ─── Progress
async function loadProgress(){
  try{
    const progress=await api('/api/admin/progress');
    // 动态表头
    const stages=progress.length?progress[0].stage_progress.map(s=>s.stage_name):[];
    document.getElementById('progress-head').innerHTML=`<tr><th>评委</th><th>角色</th>${stages.map(s=>`<th>${escHtml(s)}</th>`).join('')}<th>个人点评</th></tr>`;
    document.getElementById('progress-body').innerHTML=progress.map(e=>{
      const roleMap={cxo:'CXO',expert:'评审专家',observer:'观察员'};
      const displayRole=e.role_display||roleMap[e.role_type]||e.role_type;
      return `<tr>
        <td>${escHtml(e.name)}</td><td>${escHtml(displayRole)}</td>
        ${e.stage_progress.map(sp=>`<td>${sp.submitted?'<span class="progress-badge pb-done">✅ 已提交</span>':'<span class="progress-badge pb-pending">⏳ 未提交</span>'}</td>`).join('')}
        <td>${e.review_count}/${e.review_total}</td>
      </tr>`;
    }).join('');
  }catch(e){toast('加载进度失败：'+e.message);}
}

// ─── Export & Clear
function exportData(type){window.location.href='/api/export/'+type;}

async function clearScoresConfirm(){
  const first=confirm('⚠️ 确认清空所有打分数据？\n\n将删除：所有团队打分 + 所有点评记录\n保留：队伍、选手、评委信息不变\n\n此操作不可撤销！');
  if(!first)return;
  const second=confirm('🔴 二次确认：真的要清空吗？');
  if(!second)return;
  try{
    const res=await fetch('/api/admin/clear_scores',{method:'POST'});
    const data=await res.json();
    if(data.ok){
      alert('✅ '+data.message);
      loadAdminRanking();
    }else{alert('❌ '+(data.error||'未知错误'));}
  }catch(e){alert('❌ 请求失败');}
}

function closeModal(id){document.getElementById(id).style.display='none';}

init();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return PAGE_TEMPLATE

if __name__ == "__main__":
    init_db()
    seed_data()
    print("=" * 50)
    print("决赛打分系统已启动 v1.0.0")
    print("访问地址: http://localhost:5000")
    print("管理员: 用户名=管理员, 密码=admin123")
    print("=" * 50)
    app.run(debug=False, port=5000, host="0.0.0.0")
