#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت إشعارات المنصة متعدد المستخدمين (Multi-User CIS LMS Notifier)
يقرأ قائمة الطلاب المشتركين من قاعدة بيانات Supabase (أو المتغيرات المحلية)،
يفحص حساب كل طالب على منصة lmscis.com، ويرسل لكل طالب إشعاراته الخاصة على بريده الشخصي.
"""

import html
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# إعدادات المنصة وخادم البريد المركزي (تُقرأ من GitHub Secrets)
LMS_BASE_URL = os.getenv("LMS_BASE_URL", "https://lmscis.com").rstrip("/")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").replace(" ", "")

# إعدادات قاعدة بيانات Supabase لجلب الطلاب المشتركين في الموقع
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

CAIRO_TZ = ZoneInfo("Africa/Cairo")

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
        candidates = [self.username]
        if "@" in self.username:
            short_user = self.username.split("@")[0]
            if short_user not in candidates:
                candidates.append(short_user)

        for candidate in candidates:
            if self._attempt_login(candidate):
                return True
        return False

    def get_enrolled_courses(self) -> List[Dict[str, str]]:
        courses: Dict[str, Dict[str, str]] = {}

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
            except Exception:
                pass

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
                            continue
                        name = " ".join(link.get_text().split())
                        if name and cid not in courses:
                            courses[cid] = {
                                "id": cid,
                                "name": name,
                                "url": f"{self.base_url}/course/view.php?id={cid}",
                            }
                except Exception:
                    pass

        return list(courses.values())

    def parse_forum_discussions(
        self, forum_url: str, course_name: str, forum_title: str
    ) -> List[Dict[str, str]]:
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
        except Exception:
            pass
        return items

    def _fetch_discussion_snippet(self, discuss_url: str) -> str:
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
        course_name = course["name"]
        course_url = course["url"]
        discovered: List[Dict[str, str]] = []

        try:
            resp = self.session.get(course_url, timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")

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

                for hidden in link.find_all("span", class_="accesshide"):
                    hidden.decompose()
                event_name = " ".join(link.get_text().split()) or f"نشاط جديد #{cmid}"

                activity_container = link.find_parent("li", class_=re.compile(r"activity"))
                details = ""
                if activity_container:
                    dates_div = activity_container.find(
                        "div", attrs={"data-region": "activity-dates"}
                    ) or activity_container.find(
                        "div", class_=re.compile(r"description|activity-altcontent")
                    )
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

                if mod_type == "forum":
                    discovered.extend(
                        self.parse_forum_discussions(href, course_name, event_name)
                    )
        except Exception:
            pass

        return discovered

    def scan_all(self) -> List[Dict[str, str]]:
        all_items: List[Dict[str, str]] = []
        courses = self.get_enrolled_courses()
        for course in courses:
            all_items.extend(self.scan_course(course))
        return all_items


def build_email_html(new_items: List[Dict[str, str]]) -> str:
    now_str = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %I:%M %p")
    cards_html = ""
    for item in new_items:
        course_name = safe_email_text(item.get("course_name", ""))
        event_name = safe_email_text(item.get("event_name", ""))
        type_label = safe_email_text(item.get("type_label", ""))
        details = safe_email_text(item.get("details", ""))
        item_url = safe_email_url(item.get("url", ""))

        badge_color = "#0b57d0"
        if item["type"] == "quiz":
            badge_color = "#d93025"
        elif item["type"] == "assign":
            badge_color = "#e37400"
        elif item["type"] in ("forum", "discussion"):
            badge_color = "#188038"

        details_block = (
            f'<div style="margin-top:10px;padding:10px;background:#f8f9fa;border-right:3px solid {badge_color};border-radius:4px;font-size:14px;">'
            f"<strong>التفاصيل:</strong><br>{details}</div>"
            if details
            else ""
        )
        link_block = (
            f'<div style="margin-top:14px;"><a href="{item_url}" style="display:inline-block;background:#0b57d0;color:#fff;text-decoration:none;padding:8px 16px;border-radius:6px;font-size:14px;font-weight:bold;">فتح الحدث على المنصة ⬅</a></div>'
            if item_url
            else ""
        )

        cards_html += f"""
        <div style="border:1px solid #dadce0;border-radius:8px;padding:16px;margin-bottom:16px;background:#fff;">
            <span style="background:{badge_color};color:#fff;padding:4px 10px;border-radius:12px;font-size:12px;font-weight:bold;">{type_label}</span>
            <table style="width:100%;border-collapse:collapse;margin-top:10px;font-size:15px;">
                <tr><td style="padding:5px 0;color:#5f6368;width:110px;"><strong>📚 اسم المادة:</strong></td><td style="font-weight:bold;">{course_name}</td></tr>
                <tr><td style="padding:5px 0;color:#5f6368;"><strong>📌 اسم الحدث:</strong></td><td style="color:#0b57d0;font-weight:bold;">{event_name}</td></tr>
            </table>
            {details_block}
            {link_block}
        </div>
        """

    return f"""
    <!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"></head>
    <body style="font-family:Tahoma,Arial,sans-serif;direction:rtl;text-align:right;background:#f1f3f4;padding:20px;">
        <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;">
            <div style="background:#1a73e8;color:#fff;padding:18px 24px;">
                <h2 style="margin:0;font-size:20px;">🔔 إشعار جديد من منصة المعهد (CIS LMS)</h2>
                <p style="margin:6px 0 0;font-size:13px;">توقيت الفحص (القاهرة): {now_str} | عدد التحديثات: {len(new_items)}</p>
            </div>
            <div style="padding:20px;">{cards_html}</div>
        </div>
    </body></html>
    """


def send_email_to_student(receiver_email: str, new_items: List[Dict[str, str]]) -> None:
    if not new_items or not receiver_email:
        return

    if len(new_items) == 1:
        first = new_items[0]
        subject = f"🔔 [{first['course_name']}] {first['type_label']}: {first['event_name']}"
    else:
        courses_summary = "، ".join(sorted({i["course_name"] for i in new_items}))
        subject = f"🔔 ({len(new_items)}) تحديثات جديدة على المنصة - المواد: {courses_summary}"
    subject = subject.replace("\r", " ").replace("\n", " ")

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_SENDER
    msg["To"] = receiver_email
    msg["Subject"] = subject
    msg.attach(MIMEText(build_email_html(new_items), "html", "utf-8"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_SENDER, [receiver_email], msg.as_string())


def fetch_active_subscribers() -> List[Dict]:
    """جلب قائمة الطلاب المشتركين من جدول subscribers في Supabase."""
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/subscribers?active=eq.true&select=*"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_subscriber_seen_ids(sub_id: int, seen_ids: List[str]) -> None:
    """تحديث قائمة العناصر التي تمت رؤيتها لكل طالب في Supabase."""
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{SUPABASE_URL}/rest/v1/subscribers?id=eq.{sub_id}"
    requests.patch(url, headers=headers, json={"seen_ids": seen_ids}, timeout=30)


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables.")

    subscribers = fetch_active_subscribers()
    print(f"👥 جاري فحص حسابات {len(subscribers)} طالب مشترك...")

    for sub in subscribers:
        sub_id = sub["id"]
        username = sub["lms_username"]
        password = sub["lms_password"]
        email = sub["email"]
        seen_ids = set(sub.get("seen_ids") or [])

        notifier = LMSNotifier(LMS_BASE_URL, username, password)
        if not notifier.login():
            print(f"⚠️ تعذر تسجيل الدخول لأحد المشتركين (ID: {sub_id}).")
            continue

        all_items = notifier.scan_all()
        is_first_run = len(seen_ids) == 0
        new_items = [item for item in all_items if item["id"] not in seen_ids]

        if new_items:
            items_to_send = new_items[:4] if is_first_run else new_items
            send_email_to_student(email, items_to_send)
            print(f"📩 تم إرسال ({len(items_to_send)}) إشعار للمشترك رقم #{sub_id}.")

        for item in all_items:
            seen_ids.add(item["id"])
        update_subscriber_seen_ids(sub_id, sorted(seen_ids))


if __name__ == "__main__":
    main()
