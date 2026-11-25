import unittest, tempfile, os, sys, re, sqlite3, secrets

_SUITE_INSTANCE = tempfile.TemporaryDirectory()
_previous_instance = os.environ.get("INSTANCE_DIR")
_previous_env = os.environ.get("APP_ENV")
os.environ["INSTANCE_DIR"] = _SUITE_INSTANCE.name
os.environ["APP_ENV"] = "development"

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as module

if _previous_instance is None:
    os.environ.pop("INSTANCE_DIR", None)
else:
    os.environ["INSTANCE_DIR"] = _previous_instance
if _previous_env is None:
    os.environ.pop("APP_ENV", None)
else:
    os.environ["APP_ENV"] = _previous_env
ADMIN_PASSWORD = secrets.token_urlsafe(24)
PARTICIPANT_PASSWORD = secrets.token_urlsafe(24)
CHANGED_PASSWORD = secrets.token_urlsafe(24)
ANOTHER_PASSWORD = secrets.token_urlsafe(24)
from werkzeug.security import generate_password_hash


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.original = module.DB
        self.temp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp.close()
        a = sqlite3.connect(self.original)
        b = sqlite3.connect(self.temp.name)
        a.backup(b)
        a.close()
        b.execute("DELETE FROM attendance")
        b.execute("DELETE FROM content")
        b.execute("DELETE FROM users WHERE role!='admin'")
        b.execute("DELETE FROM attempts")
        b.execute(
            "UPDATE users SET password=? WHERE role=?",
            (generate_password_hash(ADMIN_PASSWORD), "admin"),
        )
        b.commit()
        b.close()
        module.DB = self.temp.name
        module.app.config["TESTING"] = True
        self.c = module.app.test_client()

    def tearDown(self):
        module.DB = self.original
        os.unlink(self.temp.name)

    def csrf(self, path):
        r = self.c.get(path)
        return re.search(
            r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True)
        ).group(1)

    def register(self, age="23"):
        return self.c.post(
            "/register",
            data=dict(
                csrf_token=self.csrf("/register"),
                name="Тестовый Участник",
                age=age,
                city="Казань",
                education="Высшее",
                phone="+0 000 000 00 00",
                email="test@example.org",
                experience="Нет опыта",
                motivation="Хочу помогать",
                password=PARTICIPANT_PASSWORD,
                consent="yes",
            ),
        )

    def admin(self):
        return self.c.post(
            "/login",
            data=dict(
                csrf_token=self.csrf("/login"),
                email="admin@local.test",
                password=ADMIN_PASSWORD,
            ),
        )

    def test_public_and_guards(self):
        for path in ["/", "/register", "/login", "/recover", "/privacy"]:
            self.assertEqual(self.c.get(path).status_code, 200)
        self.assertEqual(self.c.get("/admin").status_code, 302)
        self.assertEqual(self.c.get("/admin/export/csv").status_code, 302)
        self.assertEqual(self.c.post("/register", data={}).status_code, 400)

    def test_reset_revokes_other_sessions(self):
        result = self.register()
        code = re.search(
            r'class="recovery-code">([^<]+)', result.get_data(as_text=True)
        ).group(1)
        other = module.app.test_client()
        token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            other.get("/login").get_data(as_text=True),
        ).group(1)
        other.post(
            "/login",
            data=dict(
                csrf_token=token,
                email="test@example.org",
                password=PARTICIPANT_PASSWORD,
            ),
        )
        self.assertEqual(other.get("/account").status_code, 200)
        self.c.post(
            "/recover",
            data=dict(
                csrf_token=self.csrf("/recover"),
                email="test@example.org",
                code=code,
                password=CHANGED_PASSWORD,
            ),
        )
        self.assertEqual(other.get("/account").status_code, 302)

    def test_private_resources_are_not_served(self):
        for path in [
            "/instance/project.sqlite3",
            "/instance/session.key",
            "/instance/admin-access.txt",
            "/.env",
            "/app.py",
        ]:
            self.assertEqual(self.c.get(path).status_code, 404)
        response = self.c.get("/")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_admin_filters_content_and_deletion(self):
        self.register()
        with module.app.app_context():
            participant = (
                module.db()
                .execute("SELECT * FROM users WHERE role='participant'")
                .fetchone()
            )
        self.c.post("/logout", data={"csrf_token": self.csrf("/account")})
        self.admin()

        def post(**data):
            data["csrf_token"] = self.csrf("/admin")
            return self.c.post("/admin", data=data)

        post(
            action="status",
            id=participant["id"],
            status="Резерв",
            group_name="Группа 2",
        )
        self.assertIn(
            "Тестовый Участник",
            self.c.get("/admin?q=test@example.org&status=Резерв").get_data(
                as_text=True
            ),
        )
        self.assertNotIn(
            "Тестовый Участник",
            self.c.get("/admin?status=Одобрена").get_data(as_text=True),
        )
        post(
            action="content",
            kind="Документ",
            title="Личный документ",
            group_name=participant["number"],
            url="https://example.org/document",
        )
        post(
            action="content",
            kind="Материал",
            title="Небезопасный ресурс",
            url="javascript:alert(1)",
        )
        with module.app.app_context():
            self.assertIsNone(
                module.db()
                .execute("SELECT id FROM content WHERE title='Небезопасный ресурс'")
                .fetchone()
            )
        post(action="delete_user", id=participant["id"])
        with module.app.app_context():
            self.assertIsNone(
                module.db()
                .execute("SELECT id FROM users WHERE id=?", (participant["id"],))
                .fetchone()
            )
            self.assertIsNone(
                module.db()
                .execute("SELECT id FROM content WHERE title='Личный документ'")
                .fetchone()
            )

    def test_profile_and_password_change(self):
        self.register()
        response = self.c.post(
            "/account",
            data=dict(
                csrf_token=self.csrf("/account"),
                name="Обновлённое имя",
                email="updated@example.org",
                city="Казань",
                phone="+00000000000",
                current_password="wrong",
            ),
        )
        self.assertIn("текущий пароль", response.get_data(as_text=True))
        response = self.c.post(
            "/account",
            data=dict(
                csrf_token=self.csrf("/account"),
                name="Обновлённое имя",
                email="updated@example.org",
                city="Казань",
                phone="+00000000000",
                current_password=PARTICIPANT_PASSWORD,
                new_password=CHANGED_PASSWORD,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("Обновлённое имя", self.c.get("/account").get_data(as_text=True))
        self.c.post("/logout", data={"csrf_token": self.csrf("/account")})
        response = self.c.post(
            "/login",
            data=dict(
                csrf_token=self.csrf("/login"),
                email="updated@example.org",
                password=CHANGED_PASSWORD,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.c.get("/account").status_code, 200)

    def test_participant_cannot_mutate_admin_data(self):
        self.register()
        response = self.c.post(
            "/admin",
            data=dict(
                csrf_token=self.csrf("/account"),
                action="content",
                kind="Материал",
                title="Несанкционированная публикация",
            ),
        )
        self.assertEqual(response.status_code, 403)
        for fmt in ["csv", "xlsx"]:
            self.assertEqual(self.c.get("/admin/export/" + fmt).status_code, 403)
        with module.app.app_context():
            self.assertEqual(
                module.db().execute("SELECT count(*) FROM content").fetchone()[0], 0
            )

    def test_rate_limit(self):
        with module.app.app_context():
            import time

            module.db().executemany(
                "INSERT INTO attempts VALUES(?,?)", [("127.0.0.1", time.time())] * 25
            )
            module.db().commit()
        result = self.c.post(
            "/login",
            data=dict(
                csrf_token=self.csrf("/login"),
                email="test@example.org",
                password=PARTICIPANT_PASSWORD,
            ),
        )
        self.assertIn("Слишком много попыток", result.get_data(as_text=True))

    def test_underage(self):
        r = self.register("17")
        self.assertIn("с 18 лет", r.get_data(as_text=True))
        with module.app.app_context():
            self.assertEqual(
                module.db()
                .execute("SELECT count(*) FROM users WHERE role='participant'")
                .fetchone()[0],
                0,
            )

    def test_registration_recovery_and_roles(self):
        r = self.register()
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("ТБГ-", html)
        code = re.search(r'class="recovery-code">([^<]+)', html).group(1)
        self.assertEqual(self.c.get("/account").status_code, 200)
        self.assertEqual(self.c.get("/admin").status_code, 403)
        self.assertEqual(self.c.get("/admin/export/xlsx").status_code, 403)
        r = self.c.post(
            "/recover",
            data=dict(
                csrf_token=self.csrf("/recover"),
                email="test@example.org",
                code=code,
                password=CHANGED_PASSWORD,
            ),
        )
        self.assertIn("Всё получилось", r.get_data(as_text=True))
        newcode = re.search(
            r'class="recovery-code">([^<]+)', r.get_data(as_text=True)
        ).group(1)
        self.assertNotEqual(code, newcode)
        r = self.c.post(
            "/recover",
            data=dict(
                csrf_token=self.csrf("/recover"),
                email="test@example.org",
                code=code,
                password=ANOTHER_PASSWORD,
            ),
        )
        self.assertIn("Проверьте", r.get_data(as_text=True))

    def test_admin_workflow_and_isolation(self):
        self.register()
        with module.app.app_context():
            uid = (
                module.db()
                .execute("SELECT id FROM users WHERE email='test@example.org'")
                .fetchone()[0]
            )
        self.c.post("/logout", data={"csrf_token": self.csrf("/account")})
        self.admin()
        self.assertEqual(self.c.get("/admin").status_code, 200)

        def post(**kw):
            kw["csrf_token"] = self.csrf("/admin")
            r = self.c.post("/admin", data=kw)
            self.assertEqual(r.status_code, 302)

        post(action="status", id=uid, status="Обучается", group_name="Группа 1")
        post(
            action="content",
            kind="Занятие",
            title="Первое занятие",
            body="Аудитория 1",
            group_name="Группа 1",
            date="2026-12-01T10:00",
        )
        post(
            action="content",
            kind="Материал",
            title="Скрытый материал",
            body="Другая группа",
            group_name="Группа 2",
        )
        with module.app.app_context():
            cid = (
                module.db()
                .execute("SELECT id FROM content WHERE kind='Занятие'")
                .fetchone()[0]
            )
        post(
            action="edit_content",
            id=cid,
            title="Введение",
            body="Материал обновлён",
            group_name="Группа 1",
            date="2026-12-01T10:00",
        )
        post(action="attendance", user_id=uid, content_id=cid, done="on")
        self.assertEqual(self.c.get("/admin/export/csv").status_code, 200)
        self.assertTrue(self.c.get("/admin/export/xlsx").data.startswith(b"PK"))
        self.c.post("/logout", data={"csrf_token": self.csrf("/account")})
        self.c.post(
            "/login",
            data=dict(
                csrf_token=self.csrf("/login"),
                email="test@example.org",
                password=PARTICIPANT_PASSWORD,
            ),
        )
        html = self.c.get("/account").get_data(as_text=True)
        self.assertIn("Введение", html)
        self.assertIn("Посещение подтверждено", html)
        self.assertNotIn("Скрытый материал", html)

    def test_contact_saved_and_escaped(self):
        r = self.c.post(
            "/contact",
            data=dict(
                csrf_token=self.csrf("/"),
                name="Команда",
                email="partner@example.org",
                message="<script>alert(1)</script>",
                consent="yes",
            ),
        )
        self.assertEqual(r.status_code, 302)
        self.admin()
        html = self.c.get("/admin").get_data(as_text=True)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
