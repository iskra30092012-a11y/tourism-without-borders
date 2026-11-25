import os, sqlite3, secrets, functools, io, csv, re
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    g,
    send_file,
    abort,
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook

BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCE = os.path.abspath(
    os.environ.get("INSTANCE_DIR") or os.path.join(BASE, "instance")
)
os.makedirs(INSTANCE, mode=0o700, exist_ok=True)
app = Flask(__name__)
keyfile = os.path.join(INSTANCE, "session.key")
secret_key = os.environ.get("SECRET_KEY")
production = os.environ.get("APP_ENV") == "production"
if production and (not secret_key or len(secret_key) < 32):
    raise RuntimeError("Production requires SECRET_KEY of at least 32 characters.")
if not secret_key:
    if not os.path.exists(keyfile):
        with open(keyfile, "x") as key:
            os.chmod(keyfile, 0o600)
            key.write(secrets.token_hex(32))
    with open(keyfile) as key:
        secret_key = key.read().strip()
app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1" if production else "0")
    == "1",
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=1024 * 1024,
)
CSRFProtect(app)
DB = os.path.join(INSTANCE, "project.sqlite3")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close(e):
    if "db" in g:
        g.db.close()


def init():
    with app.app_context():
        db().executescript(
            """CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,recovery TEXT NOT NULL,role TEXT DEFAULT 'participant',age INTEGER,city TEXT,education TEXT,phone TEXT,experience TEXT,motivation TEXT,number TEXT UNIQUE,status TEXT DEFAULT 'Новая',group_name TEXT DEFAULT '',created TEXT,consent TEXT);
CREATE TABLE IF NOT EXISTS content(id INTEGER PRIMARY KEY,kind TEXT,title TEXT,body TEXT,date TEXT DEFAULT '',group_name TEXT DEFAULT '',url TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS attendance(user_id INTEGER,content_id INTEGER,done INTEGER DEFAULT 0,PRIMARY KEY(user_id,content_id),FOREIGN KEY(user_id) REFERENCES users(id),FOREIGN KEY(content_id) REFERENCES content(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,name TEXT,email TEXT,message TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS attempts(ip TEXT,time REAL);"""
        )
        db().commit()
        columns = {row["name"] for row in db().execute("PRAGMA table_info(users)")}
        if "auth_version" not in columns:
            db().execute(
                "ALTER TABLE users ADD COLUMN auth_version TEXT NOT NULL DEFAULT ''"
            )
            db().commit()
        db().execute("BEGIN IMMEDIATE")
        if not db().execute("SELECT id FROM users WHERE role='admin'").fetchone():
            password = secrets.token_urlsafe(18)
            db().execute(
                "INSERT INTO users(name,email,password,recovery,role,created) VALUES(?,?,?,?,?,?)",
                (
                    "Администратор",
                    "admin@local.test",
                    generate_password_hash(password),
                    generate_password_hash(secrets.token_urlsafe(24)),
                    "admin",
                    datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
                ),
            )
            db().commit()
            path = os.path.join(INSTANCE, "admin-access.txt")
            open(path, "w").write(
                "Вход: /login\nEmail: admin@local.test\nПароль: "
                + password
                + "\nИзмените email и пароль в профиле. Не публикуйте этот файл.\n"
            )
            os.chmod(path, 0o600)
        db().commit()
        os.chmod(DB, 0o600)


init()


@app.before_request
def user():
    g.user = (
        db()
        .execute("SELECT * FROM users WHERE id=?", (session.get("uid", 0),))
        .fetchone()
    )
    if g.user and session.get("auth_version", "") != g.user["auth_version"]:
        session.clear()
        g.user = None


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "SAMEORIGIN"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not request.path.startswith("/static/"):
        r.headers["Cache-Control"] = "no-store"
    r.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
    )
    return r


@app.context_processor
def context():
    return dict(
        settings={r["key"]: r["value"] for r in db().execute("SELECT * FROM settings")}
    )


def require(admin=False):
    def dec(f):
        @functools.wraps(f)
        def wrapped(*a, **kw):
            if not g.user:
                return redirect(url_for("login"))
            if admin and g.user["role"] != "admin":
                abort(403)
            return f(*a, **kw)

        return wrapped

    return dec


def limited():
    import time

    ip = request.remote_addr
    t = time.time()
    db().execute("DELETE FROM attempts WHERE time<?", (t - 900,))
    n = db().execute("SELECT count(*) FROM attempts WHERE ip=?", (ip,)).fetchone()[0]
    db().execute("INSERT INTO attempts VALUES(?,?)", (ip, t))
    db().commit()
    return n >= 25


@app.route("/")
def home():
    partners = db().execute("SELECT * FROM content WHERE kind='Партнёр'").fetchall()
    return render_template("home.html", partners=partners)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        error = None
        try:
            age = int(f.get("age", "0"))
        except ValueError:
            age = 0
        email = f.get("email", "").strip().lower()
        password = f.get("password", "")
        if limited():
            error = "Слишком много попыток. Попробуйте через 15 минут."
        elif not 18 <= age <= 120:
            error = "Участие доступно с 18 лет. Проверьте возраст."
        elif not all(
            f.get(k, "").strip()
            for k in ["name", "city", "education", "phone", "motivation"]
        ):
            error = "Заполните обязательные поля."
        elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            error = "Укажите корректный email."
        elif len(re.sub(r"\D", "", f.get("phone", ""))) < 10:
            error = "Проверьте номер телефона."
        elif len(password) < 10:
            error = "Пароль должен содержать не менее 10 символов."
        elif f.get("consent") != "yes":
            error = "Необходимо согласие на обработку данных."
        elif db().execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            error = "Этот email уже зарегистрирован. Войдите или восстановите доступ."
        if error:
            flash(error, "error")
        else:
            code = secrets.token_urlsafe(20)
            number = (
                "ТБГ-"
                + datetime.now(ZoneInfo("Europe/Moscow")).strftime("%y")
                + "-"
                + secrets.token_hex(6).upper()
            )
            cur = db().execute(
                """INSERT INTO users(name,email,password,recovery,age,city,education,phone,experience,motivation,number,created,consent) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f["name"].strip(),
                    email,
                    generate_password_hash(password),
                    generate_password_hash(code),
                    age,
                    f["city"],
                    f["education"],
                    f["phone"],
                    f.get("experience", ""),
                    f["motivation"],
                    number,
                    datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
                    "policy-v1; " + datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
                ),
            )
            db().commit()
            session.clear()
            session["uid"] = cur.lastrowid
            return render_template("success.html", number=number, code=code)
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if limited():
            flash("Слишком много попыток. Повторите через 15 минут.", "error")
        else:
            u = (
                db()
                .execute(
                    "SELECT * FROM users WHERE email=?",
                    (request.form.get("email", "").lower().strip(),),
                )
                .fetchone()
            )
            if u and check_password_hash(
                u["password"], request.form.get("password", "")
            ):
                session.clear()
                session["uid"] = u["id"]
                session["auth_version"] = u["auth_version"]
                return redirect("/admin" if u["role"] == "admin" else "/account")
            flash("Неверный email или пароль.", "error")
    return render_template("auth.html", recover=False)


@app.route("/recover", methods=["GET", "POST"])
def recover():
    if request.method == "POST":
        f = request.form
        u = (
            db()
            .execute(
                "SELECT * FROM users WHERE email=?",
                (f.get("email", "").lower().strip(),),
            )
            .fetchone()
        )
        if limited():
            flash("Повторите попытку через 15 минут.", "error")
        elif (
            u
            and check_password_hash(u["recovery"], f.get("code", ""))
            and len(f.get("password", "")) >= 10
        ):
            code = secrets.token_urlsafe(20)
            version = secrets.token_hex(16)
            updated = db().execute(
                "UPDATE users SET password=?,recovery=?,auth_version=? WHERE id=? AND recovery=?",
                (
                    generate_password_hash(f["password"]),
                    generate_password_hash(code),
                    version,
                    u["id"],
                    u["recovery"],
                ),
            )
            db().commit()
            if updated.rowcount != 1:
                flash("Код уже использован. Используйте новый резервный код.", "error")
                return redirect("/recover")
            session.clear()
            session["uid"] = u["id"]
            session["auth_version"] = version
            return render_template(
                "success.html", number=u["number"] or "Администратор", code=code
            )
        else:
            flash(
                "Проверьте email, код восстановления и длину нового пароля (от 10 символов).",
                "error",
            )
    return render_template("auth.html", recover=True)


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/account", methods=["GET", "POST"])
@require()
def account():
    if request.method == "POST":
        f = request.form
        if not check_password_hash(g.user["password"], f.get("current_password", "")):
            flash("Для изменений введите текущий пароль.", "error")
        elif not f.get("name", "").strip() or not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+", f.get("email", "")
        ):
            flash("Проверьте имя и email.", "error")
        elif f.get("new_password") and len(f["new_password"]) < 10:
            flash("Новый пароль: минимум 10 символов.", "error")
        else:
            try:
                version = (
                    secrets.token_hex(16)
                    if f.get("new_password")
                    else g.user["auth_version"]
                )
                db().execute(
                    "UPDATE users SET name=?,email=?,city=?,phone=?,password=?,auth_version=? WHERE id=?",
                    (
                        f["name"],
                        f["email"].lower().strip(),
                        f.get("city", ""),
                        f.get("phone", ""),
                        (
                            generate_password_hash(f["new_password"])
                            if f.get("new_password")
                            else g.user["password"]
                        ),
                        version,
                        g.user["id"],
                    ),
                )
                db().commit()
                session["auth_version"] = version
                flash("Профиль сохранён.", "success")
                return redirect("/account")
            except sqlite3.IntegrityError:
                flash("Email уже используется.", "error")
    items = (
        db()
        .execute(
            "SELECT * FROM content WHERE kind!='Партнёр' AND (group_name='' OR group_name=? OR group_name=?) ORDER BY date,id",
            (g.user["group_name"], g.user["number"] or "__none__"),
        )
        .fetchall()
    )
    done = [
        r[0]
        for r in db().execute(
            "SELECT content_id FROM attendance WHERE user_id=? AND done=1",
            (g.user["id"],),
        )
    ]
    return render_template("account.html", items=items, done=done)


@app.route("/admin", methods=["GET", "POST"])
@require(True)
def admin():
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        if action == "status":
            if f.get("status") in [
                "Новая",
                "На рассмотрении",
                "Одобрена",
                "Резерв",
                "Отклонена",
                "Обучается",
                "Завершил",
            ]:
                db().execute(
                    "UPDATE users SET status=?,group_name=? WHERE id=? AND role='participant'",
                    (f["status"], f.get("group_name", ""), f["id"]),
                )
        elif action == "delete_message":
            db().execute("DELETE FROM messages WHERE id=?", (f["id"],))
        elif action == "delete_user":
            uid = f.get("id")
            if (
                db()
                .execute(
                    "SELECT id FROM users WHERE id=? AND role='participant'", (uid,)
                )
                .fetchone()
            ):
                number = (
                    db()
                    .execute("SELECT number FROM users WHERE id=?", (uid,))
                    .fetchone()[0]
                )
                if number:
                    db().execute("DELETE FROM content WHERE group_name=?", (number,))
                db().execute("DELETE FROM attendance WHERE user_id=?", (uid,))
                db().execute("DELETE FROM users WHERE id=?", (uid,))
        elif action == "content":
            url = f.get("url", "").strip()
            if url and not url.startswith("https://"):
                flash("Ссылка должна начинаться с https://", "error")
                return redirect("/admin")
            if (
                f.get("kind")
                in ["Занятие", "Материал", "Практика", "Документ", "Партнёр"]
                and f.get("title", "").strip()
            ):
                db().execute(
                    "INSERT INTO content(kind,title,body,date,group_name,url) VALUES(?,?,?,?,?,?)",
                    (
                        f["kind"],
                        f["title"],
                        f.get("body", ""),
                        f.get("date", ""),
                        f.get("group_name", ""),
                        url,
                    ),
                )
        elif action == "edit_content":
            url = f.get("url", "").strip()
            if url and not url.startswith("https://"):
                flash("Ссылка должна начинаться с https://", "error")
                return redirect("/admin")
            if f.get("title", "").strip():
                db().execute(
                    "UPDATE content SET title=?,body=?,date=?,group_name=?,url=? WHERE id=?",
                    (
                        f["title"],
                        f.get("body", ""),
                        f.get("date", ""),
                        f.get("group_name", ""),
                        url,
                        f["id"],
                    ),
                )
        elif action == "delete":
            db().execute("DELETE FROM content WHERE id=?", (f["id"],))
        elif action == "attendance":
            db().execute(
                "INSERT INTO attendance(user_id,content_id,done) VALUES(?,?,?) ON CONFLICT(user_id,content_id) DO UPDATE SET done=excluded.done",
                (f["user_id"], f["content_id"], 1 if f.get("done") else 0),
            )
        elif action == "settings":
            for k in ["operator", "contact", "address"]:
                db().execute(
                    "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, f.get(k, "")),
                )
        db().commit()
        flash("Изменения сохранены.", "success")
        return redirect("/admin")
    q = request.args.get("q", "")
    status = request.args.get("status", "")
    users = (
        db()
        .execute(
            "SELECT * FROM users WHERE role='participant' AND (name LIKE ? OR email LIKE ? OR number LIKE ?) AND (?='' OR status=?) ORDER BY id DESC",
            ("%" + q + "%", "%" + q + "%", "%" + q + "%", status, status),
        )
        .fetchall()
    )
    allusers = db().execute("SELECT * FROM users WHERE role='participant'").fetchall()
    return render_template(
        "admin.html",
        users=users,
        allusers=allusers,
        items=db().execute("SELECT * FROM content ORDER BY id DESC").fetchall(),
        messages=db().execute("SELECT * FROM messages ORDER BY id DESC").fetchall(),
        attendance={
            (r["user_id"], r["content_id"]): r["done"]
            for r in db().execute("SELECT * FROM attendance")
        },
    )


@app.get("/admin/export/<fmt>")
@require(True)
def export(fmt):
    if fmt not in ["csv", "xlsx"]:
        abort(404)
    rows = (
        db()
        .execute(
            "SELECT number,name,age,city,education,phone,email,experience,motivation,status,group_name,created,consent FROM users WHERE role='participant'"
        )
        .fetchall()
    )
    headings = [
        "Номер",
        "ФИО",
        "Возраст",
        "Город",
        "Образование",
        "Телефон",
        "Email",
        "Волонтёрство",
        "Мотивация",
        "Статус",
        "Группа",
        "Дата",
        "Согласие",
    ]
    data = [headings] + [
        ["'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v for v in r]
        for r in rows
    ]
    if fmt == "csv":
        s = io.StringIO()
        csv.writer(s, delimiter=";").writerows(data)
        buf = io.BytesIO(s.getvalue().encode("utf-8-sig"))
        mime = "text/csv"
    else:
        w = Workbook()
        sheet = w.active
        sheet.title = "Заявки"
        for row in data:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        buf = io.BytesIO()
        w.save(buf)
        buf.seek(0)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return send_file(
        buf,
        mimetype=mime,
        as_attachment=True,
        download_name="tourism-applications." + fmt,
    )


@app.post("/contact")
def contact():
    f = request.form
    if limited():
        flash("Слишком много сообщений. Попробуйте позже.", "error")
    elif (
        f.get("consent") == "yes"
        and f.get("name", "").strip()
        and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", f.get("email", ""))
        and f.get("message", "").strip()
    ):
        db().execute(
            "INSERT INTO messages(name,email,message,created) VALUES(?,?,?,?)",
            (
                f["name"],
                f["email"],
                f["message"],
                datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
            ),
        )
        db().commit()
        flash("Сообщение сохранено и доступно команде проекта.", "success")
    else:
        flash("Заполните поля и подтвердите согласие.", "error")
    return redirect("/#contacts")


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.errorhandler(CSRFError)
def csrf_error(e):
    return (
        render_template(
            "error.html",
            title="Обновите страницу",
            text="Срок действия формы истёк или защитный код отсутствует. Вернитесь к форме, обновите страницу и повторите отправку.",
        ),
        400,
    )


@app.errorhandler(403)
def forbidden(e):
    return (
        render_template(
            "error.html",
            title="Доступ ограничен",
            text="Этот раздел доступен только администратору.",
        ),
        403,
    )


@app.errorhandler(404)
def missing(e):
    return (
        render_template(
            "error.html",
            title="Страница не найдена",
            text="Возможно, адрес изменился. Вернитесь на главную.",
        ),
        404,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "3000")))
