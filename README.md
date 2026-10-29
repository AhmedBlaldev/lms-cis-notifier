# 🔔 CIS LMS Notifier (`lmscis.com`)
small script to fetch all events from my college platform to my email to remind me, because our platform it's sucks


Because the official Moodle platform ([lmscis.com](https://www.google.com/search?q=https://lmscis.com/)) does not send email alerts, this tool automatically checks your enrolled courses every hour and sends a clean, formatted email notification to your personal Gmail whenever a professor posts a new **Quiz**, **Assignment**, **Announcement**, **Lecture File**, or **Deadline**.

---

## ✨ Key Features

- **Automatic Course Discovery:** Automatically detects all courses you are enrolled in for the current semester.
- **Complete Activity Monitoring:** Tracks and notifies you about:
  - 📝 **Quizzes** (`mod_quiz`)
  - 📂 **Assignments** (`mod_assign`)
  - 📢 **Course & Site Announcements** (`mod_forum` & discussions)
  - 📚 **Lecture Files, Folders & Links** (`mod_resource`, `mod_folder`, `mod_url`)
  - 📅 **Upcoming Calendar Deadlines** (`calendar_event`)
- **Clear, Structured Email Alerts:** Every email clearly displays the **Course Name**, **Activity Type** (color-coded badge), **Event Title**, **Announcement Content / Due Dates**, and a direct button to open the item on the LMS.
- **100% Free & Serverless (24/7):** Runs automatically every hour via **GitHub Actions** (using Cairo local time `Africa/Cairo`) without keeping your PC on.
- **Smart Deduplication:** Saves seen item IDs in `seen_lms_items.json` so you never receive duplicate emails for old items.
- **Privacy & Security First:** All credentials are read strictly from encrypted **GitHub Secrets** and are never exposed in code or workflow logs.

---

## 🚀 Step-by-Step Setup Guide for Students

Any student at CIS can set up their own personal notifier in less than 5 minutes by following these steps in order:

### Step 1: Fork This Repository
1. Click the **Fork** button at the top-right corner of this GitHub page to create a copy of this project under your own GitHub account.
2. In your forked repository, open `seen_lms_items.json` (if it exists) and delete it—or replace its content with `{"seen_ids": []}`—so the script scans your own courses from scratch.

### Step 2: Generate a Gmail App Password
To allow the script to send email notifications to your Gmail securely without using your main Google password:
1. Go to your [Google Account Security Settings](https://www.google.com/search?q=https://myaccount.google.com/security) and make sure **2-Step Verification** is turned **ON**.
2. Open the [Google App Passwords Page](https://www.google.com/search?q=https://myaccount.google.com/apppasswords).
3. Enter a name such as `CIS LMS Notifier` and click **Create**.
4. Copy the **16-character password** shown on the screen (you will need it in Step 3).

### Step 3: Add Your Credentials to GitHub Secrets
Your LMS and email credentials are stored safely inside GitHub's encrypted secrets vault:
1. Inside your forked repository on GitHub, click the **Settings** tab at the top.
2. In the left sidebar, expand **Secrets and variables** and click **Actions**.
3. Click the green **New repository secret** button and add the following **5 secrets** one by one:

| Secret Name | Value to Enter |
| :--- | :--- |
| `LMS_USERNAME` | Your CIS student ID or LMS username (e.g., `C2300000` or `C2300000@cis.edu.eg`) |
| `LMS_PASSWORD` | Your password for `lmscis.com` |
| `EMAIL_SENDER` | Your personal Gmail address (e.g., `yourname@gmail.com`) |
| `EMAIL_APP_PASSWORD` | The 16-character Gmail App Password generated in **Step 2** |
| `EMAIL_RECEIVER` | The email address where you want to receive alerts (usually the same as `EMAIL_SENDER`) |

### Step 4: Enable & Test Your Workflow
1. Click the **Actions** tab at the top of your repository.
2. If GitHub shows a prompt saying workflows are disabled on a forked repository, click the green button: **I understand my workflows, go ahead and enable them**.
3. Select **LMS CIS Notification Checker** from the left sidebar.
4. Click the **Run workflow** dropdown button on the right, then click the green **Run workflow** button.
5. Wait about 30–45 seconds until a green checkmark (✅) appears. Check your Gmail inbox—you will find your first confirmation email containing the latest items from your courses!

From now on, GitHub Actions will automatically check the LMS every hour and email you whenever anything new is posted.

---

## ⚙️ Project Structure

| File | Description |
| :--- | :--- |
| `lms_notifier.py` | Main Python script that logs into `lmscis.com`, scans courses/forums/calendar, and sends HTML emails. |
| `.github/workflows/lms_check.yml` | GitHub Actions workflow that runs `lms_notifier.py` every hour (`0 * * * *`) and commits updated state. |
| `seen_lms_items.json` | Automatically updated JSON file storing IDs of previously seen activities to prevent duplicate alerts. |

---

## 🛠️ Running Locally (Optional)

If you want to test or run the script on your own computer instead of GitHub Actions:

1. Install Python 3.9+ and the required dependencies:
   ```bash
   pip install requests beautifulsoup4
   ```
2. Set the required environment variables and run the script:
   ```bash
   export LMS_USERNAME="your_student_id"
   export LMS_PASSWORD="your_lms_password"
   export EMAIL_SENDER="your_email@gmail.com"
   export EMAIL_APP_PASSWORD="your_16_char_app_password"
   export EMAIL_RECEIVER="your_email@gmail.com"

   python lms_notifier.py
   ```

---

## 🔒 Security & Privacy Notes

- **Never hardcode passwords** directly inside `lms_notifier.py`. Always use **GitHub Secrets** or environment variables.
- Even if your repository is **Public**, GitHub Secrets are completely hidden and cannot be viewed by anyone—nor are they copied when another student forks your repository.
- `seen_lms_items.json` only stores numerical Moodle module IDs (e.g., `mod_resource_16500`) and contains no personal data or grades.
