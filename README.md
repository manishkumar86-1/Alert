# Job Alerts (Free - GitHub Actions आधारित)

This project runs a Google job search every hour and emails new results.

## Setup (2 minutes)

1. Click **Use this template**
2. Go to **Settings → Secrets → Actions**
3. Add:

- EMAIL_USER → your Gmail
- EMAIL_PASS → Gmail App Password
- EMAIL_TO → your email

4. Go to **Actions → Run workflow**

Done ✅

## Notes
- Runs hourly
- Tracks only NEW jobs
- Uses Google "past 1 hour" filter
