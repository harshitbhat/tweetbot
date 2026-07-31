import os
import datetime
import requests
from openai import OpenAI
import tweepy

GITHUB_USER = "harshitbhat"
GH_TOKEN = os.environ["GH_PAT"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]

since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}

repos = []
page = 1
while True:
    r = requests.get(
        f"https://api.github.com/user/repos?per_page=100&page={page}&affiliation=owner",
        headers=headers,
    )
    batch = r.json()
    if not batch:
        break
    repos.extend(batch)
    page += 1

commits = []
PER_COMMIT_CAP = 15000    
MAX_COMMITS = 50            # protects rate limit, not the LLM
TOTAL_CAP = 300_000         # ~75K tokens, still comfortably inside the window

# Files that are almost always noise
SKIP_PATTERNS = (
    "package-lock.json", "yarn.lock", "pnpm-lock", "poetry.lock",
    ".min.js", ".min.css", "node_modules/", "dist/", "build/",
    ".map", ".png", ".jpg", ".pdf", ".ipynb_checkpoints",
)

def is_noise(filename):
    return any(p in filename for p in SKIP_PATTERNS)

total_chars = 0
for repo in repos:
    r = requests.get(
        f"https://api.github.com/repos/{repo['full_name']}/commits",
        headers=headers,
        params={"since": since, "author": GITHUB_USER, "per_page": 50},
    )
    if r.status_code != 200:
        continue

    for c in r.json()[:MAX_COMMITS]:
        detail = requests.get(c["url"], headers=headers).json()

        diff_parts = []
        for f in detail.get("files", []):
            if is_noise(f["filename"]):
                continue
            patch = f.get("patch", "")
            if patch:
                diff_parts.append(f"--- {f['filename']} ---\n{patch}")

        msg = c["commit"]["message"].split("\n")[0]
        diff_text = "\n".join(diff_parts)[:PER_COMMIT_CAP]

        if diff_text.strip():
            entry = f"[{repo['name']}] {msg}\n{diff_text}"
        else:
            entry = f"[{repo['name']}] {msg}"

        if total_chars + len(entry) > TOTAL_CAP:
            break
        commits.append(entry)
        total_chars += len(entry)

    if total_chars > TOTAL_CAP:
        break

commit_list = "\n\n".join(commits)
print(f"Found {len(commits)} commits, {total_chars:,} chars of diff")

if not commits:
    print("No commits in the last 24h — skipping tweet.")
    raise SystemExit(0)

# --- 2. Turn them into a tweet with OpenAI ---
client = OpenAI(api_key=OPENAI_KEY)
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {
            "role": "system",
            "content": (
                "You write build-in-public tweets for a software engineer. Given the day's "
                "commits, write ONE tweet under 270 characters that sounds like a person "
                "thinking out loud, not a standup report. Rules:\n"
                "- Lead with the interesting problem, decision, or lesson — never with "
                "'Today I...' or a summary of tasks.\n"
                "- Be specific: name the actual problem, tradeoff, or bug if one is visible.\n"
                "- It's fine to share a small opinion or hot take that follows from the work.\n"
                "- No meta-commentary about tweeting, bots, or automation itself unless that "
                "IS the project being worked on.\n"
                "- Skip trivial commits (typos, config, renames).\n"
                "- Plain text, max 1 hashtag (or none), no emojis.\n"
                "- Never use phrases like 'excited to share', 'stay tuned', 'big things coming'."
            ),
        },
        {"role": "user", "content": f"Today's commits:\n{commit_list}"},
    ],
    max_tokens=120,
)
tweet = resp.choices[0].message.content.strip().strip('"')
if len(tweet) > 280:
    tweet = tweet[:277] + "..."
print(f"\nTweet:\n{tweet}")

# --- 3. Post to Twitter ---
tw = tweepy.Client(
    consumer_key=os.environ["TW_API_KEY"],
    consumer_secret=os.environ["TW_API_SECRET"],
    access_token=os.environ["TW_ACCESS_TOKEN"],
    access_token_secret=os.environ["TW_ACCESS_SECRET"],
)
tw.create_tweet(text=tweet)
print("Tweet posted.")