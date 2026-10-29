#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت متابعة منصة المعهد العالي لعلوم الحاسب ونظم المعلومات (lmscis.com)
يقوم بتسجيل الدخول، فحص جميع المواد المسجلة والإعلانات العامة،
وإرسال إشعار فوري على البريد الإلكتروني الشخصي موضحاً فيه:
- اسم المادة (Course Name)
- نوع الحدث (كويز / أسايمنت / إعلان / محاضرة)
- اسم الحدث أو تفاصيل الإعلان والرابط المباشر له
"""

import html
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ==========================================
# إعدادات المنصة والبريد الإلكتروني
# (يتم قراءتها بالكامل من متغيرات البيئة / GitHub Secrets)
# ==========================================
LMS_BASE_URL = os.getenv("LMS_BASE_URL", "https://lmscis.com").rstrip("/")
LMS_USERNAME = os.getenv("LMS_USERNAME", "").strip()
LMS_PASSWORD = os.getenv("LMS_PASSWORD", "")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "").strip()
# إزالة المسافات تلقائياً من App Password لضمان عمله بدون مشاكل
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").replace(" ", "")
# إذا لم يتم تحديد EMAIL_RECEIVER، يتم الإرسال لنفس EMAIL_SENDER تلقائياً
EMAIL_RECEIVER = (os.getenv("EMAIL_RECEIVER") or EMAIL_SENDER).strip()

STATE_FILE = Path(os.getenv("STATE_FILE", "seen_lms_items.json"))
CAIRO_TZ = ZoneInfo("Africa/Cairo")

# ترجمة أنواع الأنشطة داخل Moodle إلى مسميات واضحة بالعربية
ACTIVITY_TYPES_AR = {
    "assign": "واجب / أسايمنت (Assignment)",
    "quiz": "اختبار قصير / كويز (Quiz)",
    "forum": "منتدى / إعلانات (Announcement Forum)",
    "discussion": "إعلان جديد من الدكتور (Announcement)",
    "resource": "ملف / محاضرة (File/Lecture)",
    "folder": "مجلد ملفات (Folder)",
    "url": "رابط خارجي (Link)",
    "page": "صفحة محتوى (Page)",
    "calendar_event": "موعد تسليم / حدث في التقويم (Deadline/Event)",
}


def validate_required_settings() -> None:
    required = {
        "LMS_USERNAME": LMS_USERNAME,
        "LMS_PASSWORD": LMS_PASSWORD,
        "EMAIL_SENDER": EMAIL_SENDER,
        "EMAIL_APP_PASSWORD": EMAIL_APP_PASSWORD,
        "EMAIL_RECEIVER": EMAIL_RECEIVER,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def safe_email_text(value: object) -> str:
    return html.escape(str(value), quote=True)


def safe_email_url(value: object) -> str:
    url = str(value).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return html.escape(url, quote=True)


class LMSNotifier:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username.strip()
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )
        self.sesskey: Optional[str] = None

    def _attempt_login(self, username_to_try: str) -> bool:
        """محاولة تسجيل الدخول باسم مستخدم محدد."""
        login_url = f"{self.base_url}/login/index.php"
        resp = self.session.get(login_url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        token_input = soup.find("input", {"name": "logintoken"})
        logintoken = token_input["value"] if token_input else ""

        payload = {
            "anchor": "",
            "logintoken": logintoken,
            "username": username_to_try,
            "password": self.password,
        }

        post_resp = self.session.post(login_url, data=payload, timeout=30)
        post_resp.raise_for_status()

        if "login/index.php" in post_resp.url and "testsession" not in post_resp.url:
            return False

        match = re.search(r'"sesskey"\s*:\s*"([^"]+)"', post_resp.text)
        if match:
            self.sesskey = match.group(1)

        return True

    def login(self) -> bool:
        """تسجيل الدخول إلى منصة Moodle (يجرب الاسم كما هو، وإذا فشل يجرب بدون @cis.edu.eg)."""
        candidates = [self.username]
        if "@" in self.username:
            short_user = self.username.split("@")[0]
            if short_user not in candidates:
                candidates.append(short_user)

        for candidate in candidates:
            if self._attempt_login(candidate):
                print("✅ تم تسجيل الدخول بنجاح إلى المنصة.")
                return True

        print("❌ فشل تسجيل الدخول: تأكد من اسم المستخدم وكلمة المرور.")
        return False

    def get_enrolled_courses(self) -> List[Dict[str, str]]:
        """جلب قائمة المواد المسجل فيها الطالب (الاسم + الرابط + المعرف)."""
        courses: Dict[str, Dict[str, str]] = {}

        # 1. المحاولة الأولى: عبر واجهة AJAX الرسمية في Moodle 4.x
        if self.sesskey:
            ajax_url = (
                f"{self.base_url}/lib/ajax/service.php"
                f"?sesskey={self.sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
            )
            payload = [
                {
                    "index": 0,
                    "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                    "args": {
                        "offset": 0,
                        "limit": 0,
                        "classification": "all",
                        "sort": "fullname",
                    },
                }
            ]
            try:
                resp = self.session.post(ajax_url, json=payload, timeout=30)
                data = resp.json()
                if isinstance(data, list) and data and not data[0].get("error"):
                    for c in data[0].get("data", {}).get("courses", []):
                        cid = str(c.get("id"))
                        courses[cid] = {
                            "id": cid,
                            "name": c.get("fullname", "").strip(),
                            "url": c.get(
                                "viewurl",
                                f"{self.base_url}/course/view.php?id={cid}",
                            ),
                        }
            except Exception as exc:
                print(f"⚠️ تنبيه أثناء جلب المواد عبر AJAX: {exc}")

        # 2. المحاولة البديلة: قراءة روابط المواد من صفحة /my/ و /my/courses.php
        if not courses:
            for path in ["/my/courses.php", "/my/", "/"]:
                try:
                    resp = self.session.get(f"{self.base_url}{path}", timeout=30)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for link in soup.find_all("a", href=re.compile(r"/course/view\.php\?id=\d+")):
                        href = link.get("href", "")
                        m = re.search(r"id=(\d+)", href)
                        if not m:
                            continue
                        cid = m.group(1)
                        if cid == "1":
                            continue  # تخطي الصفحة الرئيسية للموقع
                        name = " ".join(link.get_text().split())
                        if name and cid not in courses:
                            courses[cid] = {
                                "id": cid,
                                "name": name,
                                "url": f"{self.base_url}/course/view.php?id={cid}",
                            }
                except Exception as exc:
                    print(f"⚠️ تنبيه أثناء فحص {path}: {exc}")

        print(f"📚 تم العثور على {len(courses)} مادة مسجلة.")
        return list(courses.values())

    def parse_forum_discussions(
        self, forum_url: str, course_name: str, forum_title: str
    ) -> List[Dict[str, str]]:
        """الدخول إلى منتدى الإعلانات واستخراج أي منشورات/إعلانات جديدة كتبها الدكتور."""
        items: List[Dict[str, str]] = []
        try:
            resp = self.session.get(forum_url, timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")

            for link in soup.find_all("a", href=re.compile(r"/mod/forum/discuss\.php\?d=\d+")):
                href = link.get("href", "")
                m = re.search(r"d=(\d+)", href)
                if not m:
                    continue
                disc_id = m.group(1)
                subject = " ".join(link.get_text().split())
                if not subject or subject.lower() in {"discuss this topic", "permalink"}:
                    continue

                # تجنب التكرار لنفس المنشور في نفس الصفحة
                unique_id = f"discussion_{disc_id}"
                if any(i["id"] == unique_id for i in items):
                    continue

                items.append(
                    {
                        "id": unique_id,
                        "course_name": course_name,
                        "type": "discussion",
                        "type_label": ACTIVITY_TYPES_AR["discussion"],
                        "event_name": f"{subject} (داخل: {forum_title})",
                        "url": href.split("#")[0],
                        "details": self._fetch_discussion_snippet(href.split("#")[0]),
                    }
                )
        except Exception as exc:
            print(f"⚠️ تعذر قراءة المنتدى {forum_url}: {exc}")

        return items

    def _fetch_discussion_snippet(self, discuss_url: str) -> str:
        """قراءة نص الإعلان الذي كتبه الدكتور واسم الدكتور."""
        try:
            resp = self.session.get(discuss_url, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")
            post_content = soup.find("div", class_=re.compile(r"post-content-container|posting"))
            if post_content:
                text = " ".join(post_content.get_text(separator=" ").split())
                return text[:400] + ("..." if len(text) > 400 else "")
        except Exception:
            pass
        return ""

    def scan_course(self, course: Dict[str, str]) -> List[Dict[str, str]]:
        """فحص صفحة المادة واستخراج جميع الكويزات، الأسايمنتات، الإعلانات، والملفات."""
        course_name = course["name"]
        course_url = course["url"]
        discovered: List[Dict[str, str]] = []

        try:
            resp = self.session.get(course_url, timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")

            # البحث عن جميع الأنشطة داخل صفحة المادة
            mod_pattern = re.compile(
                r"/mod/(assign|quiz|forum|resource|folder|url|page)/view\.php\?id=(\d+)"
            )
            seen_cmids = set()

            for link in soup.find_all("a", href=mod_pattern):
                href = link.get("href", "")
                m = mod_pattern.search(href)
                if not m:
                    continue
                mod_type, cmid = m.group(1), m.group(2)
                if cmid in seen_cmids:
                    continue
                seen_cmids.add(cmid)

                # تنظيف اسم الحدث من النصوص المخفية الخاصة بقارئ الشاشة في Moodle
                for hidden in link.find_all("span", class_="accesshide"):
                    hidden.decompose()
                event_name = " ".join(link.get_text().split())
                if not event_name:
                    event_name = f"نشاط جديد #{cmid}"

                # استخراج أي تفاصيل أو مواعيد تسليم مكتوبة بجانب النشاط
                activity_container = link.find_parent("li", class_=re.compile(r"activity"))
                details = ""
                if activity_container:
                    dates_div = activity_container.find(
                        "div", attrs={"data-region": "activity-dates"}
                    ) or activity_container.find("div", class_=re.compile(r"description|activity-altcontent"))
                    if dates_div:
                        details = " ".join(dates_div.get_text(separator=" | ").split())

                item_id = f"mod_{mod_type}_{cmid}"
                discovered.append(
                    {
                        "id": item_id,
                        "course_name": course_name,
                        "type": mod_type,
                        "type_label": ACTIVITY_TYPES_AR.get(mod_type, mod_type),
                        "event_name": event_name,
                        "url": href,
                        "details": details[:350],
                    }
                )

                # إذا كان النشاط عبارة عن منتدى إعلانات (Forum)، ندخل لقراءة الإعلانات بداخله
                if mod_type == "forum":
                    discovered.extend(
                        self.parse_forum_discussions(href, course_name, event_name)
                    )

        except Exception as exc:
            print(f"⚠️ خطأ أثناء فحص مادة '{course_name}': {exc}")

        return discovered

    def scan_site_announcements_and_calendar(self) -> List[Dict[str, str]]:
        """فحص إعلانات المعهد العامة في الصفحة الرئيسية ومواعيد التقويم القادمة."""
        discovered: List[Dict[str, str]] = []

        # 1. فحص إعلانات الموقع العامة في الصفحة الرئيسية
        try:
            resp = self.session.get(f"{self.base_url}/?redirect=0", timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("a", href=re.compile(r"/mod/forum/discuss\.php\?d=\d+")):
                href = link.get("href", "")
                m = re.search(r"d=(\d+)", href)
                if not m:
                    continue
                disc_id = m.group(1)
                subject = " ".join(link.get_text().split())
                if not subject or subject.lower() in {"discuss this topic", "permalink"}:
                    continue
                unique_id = f"site_discussion_{disc_id}"
                if any(i["id"] == unique_id for i in discovered):
                    continue
                discovered.append(
                    {
                        "id": unique_id,
                        "course_name": "إعلانات المعهد العامة (الصفحة الرئيسية)",
                        "type": "discussion",
                        "type_label": "إعلان عام من المعهد / الدكاترة",
                        "event_name": subject,
                        "url": href.split("#")[0],
                        "details": self._fetch_discussion_snippet(href.split("#")[0]),
                    }
                )
        except Exception as exc:
            print(f"⚠️ تنبيه أثناء فحص الإعلانات العامة: {exc}")

        # 2. فحص الأحداث والمواعيد القادمة في التقويم (Upcoming Events)
        try:
            cal_url = f"{self.base_url}/calendar/view.php?view=upcoming"
            resp = self.session.get(cal_url, timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")
            for event_card in soup.find_all("div", attrs={"data-type": "event"}):
                event_id = event_card.get("data-event-id")
                title_el = event_card.find(["h3", "h4"], class_=re.compile(r"name"))
                event_title = " ".join(title_el.get_text().split()) if title_el else "حدث بالتقويم"

                course_link = event_card.find("a", href=re.compile(r"/course/view\.php\?id=\d+"))
                course_name = (
                    " ".join(course_link.get_text().split())
                    if course_link
                    else "تقويم المنصة العام"
                )

                date_el = event_card.find("div", class_=re.compile(r"row"))
                details = " ".join(date_el.get_text(separator=" ").split()) if date_el else ""

                action_link = event_card.find("a", class_=re.compile(r"card-link"))
                url = action_link["href"] if action_link and action_link.get("href") else cal_url

                if event_id:
                    discovered.append(
                        {
                            "id": f"cal_event_{event_id}",
                            "course_name": course_name,
                            "type": "calendar_event",
                            "type_label": ACTIVITY_TYPES_AR["calendar_event"],
                            "event_name": event_title,
                            "url": url,
                            "details": details[:300],
                        }
                    )
        except Exception as exc:
            print(f"⚠️ تنبيه أثناء فحص التقويم: {exc}")

        return discovered


def build_email_html(new_items: List[Dict[str, str]]) -> str:
    """تصميم رسالة البريد الإلكتروني بتنسيق عربي واضح يبرز اسم المادة واسم الحدث بتوقيت القاهرة."""
    now_str = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %I:%M %p")

    cards_html = ""
    for item in new_items:
        course_name = safe_email_text(item.get("course_name", ""))
        event_name = safe_email_text(item.get("event_name", ""))
        type_label = safe_email_text(item.get("type_label", ""))
        details = safe_email_text(item.get("details", ""))
        item_url = safe_email_url(item.get("url", ""))

        # تحديد لون الشارة حسب نوع الحدث
        badge_color = "#0b57d0"
        if item["type"] == "quiz":
            badge_color = "#d93025"  # أحمر للكويزات
        elif item["type"] == "assign":
            badge_color = "#e37400"  # برتقالي للأسايمنت
        elif item["type"] in ("forum", "discussion"):
            badge_color = "#188038"  # أخضر للإعلانات

        details_block = ""
        if details:
            details_block = f"""
            <div style="margin-top: 10px; padding: 10px; background-color: #f8f9fa; border-right: 3px solid {badge_color}; border-radius: 4px; color: #3c4043; font-size: 14px;">
                <strong>التفاصيل / المحتوى:</strong><br>{details}
            </div>
            """

        link_block = ""
        if item_url:
            link_block = f"""
            <div style="margin-top: 14px;">
                <a href="{item_url}" style="display: inline-block; background-color: #0b57d0; color: #ffffff; text-decoration: none; padding: 8px 16px; border-radius: 6px; font-size: 14px; font-weight: bold;">
                    فتح الحدث على المنصة ⬅
                </a>
            </div>
            """

        cards_html += f"""
        <div style="border: 1px solid #dadce0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background-color: #ffffff;">
            <div style="margin-bottom: 8px;">
                <span style="background-color: {badge_color}; color: #ffffff; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: bold;">
                    {type_label}
                </span>
            </div>
            <table style="width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 15px;">
                <tr>
                    <td style="padding: 6px 0; color: #5f6368; width: 110px;"><strong>📚 اسم المادة:</strong></td>
                    <td style="padding: 6px 0; color: #1f1f1f; font-weight: bold;">{course_name}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #5f6368;"><strong>📌 اسم الحدث:</strong></td>
                    <td style="padding: 6px 0; color: #0b57d0; font-weight: bold;">{event_name}</td>
                </tr>
            </table>
            {details_block}
            {link_block}
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head><meta charset="UTF-8"></head>
    <body style="font-family: 'Segoe UI', Tahoma, Arial, sans-serif; direction: rtl; text-align: right; background-color: #f1f3f4; padding: 20px; margin: 0;">
        <div style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.08);">
            <div style="background-color: #1a73e8; color: #ffffff; padding: 18px 24px;">
                <h2 style="margin: 0; font-size: 20px;">🔔 إشعار جديد من منصة المعهد (CIS LMS)</h2>
                <p style="margin: 6px 0 0 0; font-size: 13px; opacity: 0.9;">توقيت الفحص (القاهرة): {now_str} | عدد التحديثات الجديدة: {len(new_items)}</p>
            </div>
            <div style="padding: 20px;">
                {cards_html}
            </div>
            <div style="background-color: #f8f9fa; padding: 12px 24px; text-align: center; font-size: 12px; color: #5f6368; border-top: 1px solid #e8eaed;">
                تم الإرسال تلقائياً بواسطة منبه منصة المعهد (lmscis.com)
            </div>
        </div>
    </body>
    </html>
    """


def send_email_notification(new_items: List[Dict[str, str]]) -> None:
    """إرسال بريد إلكتروني بالتحديثات الجديدة مع وضع اسم المادة واسم الحدث في عنوان الإيميل."""
    if not new_items:
        return

    if len(new_items) == 1:
        first = new_items[0]
        subject = (
            f"🔔 [{first['course_name']}] {first['type_label']}: "
            f"{first['event_name']}"
        )
    else:
        courses_summary = "، ".join(sorted({item["course_name"] for item in new_items}))
        subject = f"🔔 ({len(new_items)}) تحديثات جديدة على المنصة - المواد: {courses_summary}"
    subject = subject.replace("\r", " ").replace("\n", " ")

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = subject

    html_body = build_email_html(new_items)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_SENDER, [EMAIL_RECEIVER], msg.as_string())

    print(f"📩 تم إرسال الإشعار بنجاح ({len(new_items)} عنصر جديد).")


def load_seen_ids() -> set:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen_ids", []))
        except Exception:
            return set()
    return set()


def save_seen_ids(seen_ids: set) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(CAIRO_TZ).isoformat(),
                "seen_ids": sorted(seen_ids),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():
    validate_required_settings()
    notifier = LMSNotifier(LMS_BASE_URL, LMS_USERNAME, LMS_PASSWORD)
    if not notifier.login():
        raise SystemExit(1)

    all_items: List[Dict[str, str]] = []

    # 1. فحص الإعلانات العامة والتقويم
    all_items.extend(notifier.scan_site_announcements_and_calendar())

    # 2. فحص جميع المواد المسجل فيها الطالب
    courses = notifier.get_enrolled_courses()
    for course in courses:
        all_items.extend(notifier.scan_course(course))

    seen_ids = load_seen_ids()
    is_first_run = len(seen_ids) == 0

    new_items = [item for item in all_items if item["id"] not in seen_ids]

    if is_first_run and os.getenv("SEND_ON_FIRST_RUN", "true").lower() != "true":
        print(
            f"ℹ️ التشغيل الأول: تم حفظ {len(all_items)} عنصر حالي كمرجع دون إرسال إيميل قديم."
        )
    elif new_items:
        # في أول تشغيل، نرسل آخر 5 عناصر كرسالة تأكيد لتجربة النظام
        items_to_send = new_items[:5] if is_first_run else new_items
        send_email_notification(items_to_send)
    else:
        print("✔️ لا توجد إعلانات أو كويزات أو واجبات جديدة منذ آخر فحص.")

    for item in all_items:
        seen_ids.add(item["id"])
    save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()