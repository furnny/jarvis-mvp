# Landing Page — Deploy Guide

One HTML file. No build step, no server. Deploys in ~5 minutes.

---

## Step 1 — Set up email capture (Formspree)

This handles the waitlist form so you never need to run a backend.

1. Go to **https://formspree.io** → sign up free
2. Click **New Form** → name it "Jarvis Waitlist" → copy the form ID
   (looks like `xpzgkwbn`)
3. Open `landing/index.html` in a text editor (TextEdit/Notepad is fine)
4. Find this line:
   ```
   action="https://formspree.io/f/YOUR_FORMSPREE_ID"
   ```
   Replace `YOUR_FORMSPREE_ID` with your form ID:
   ```
   action="https://formspree.io/f/xpzgkwbn"
   ```
5. Save the file

Every email signup will land in your Formspree dashboard (and can forward to
your email). Formspree free tier allows 50 submissions/month; upgrade if it
fills up.

---

## Step 2 — Deploy to Vercel (recommended — fastest)

1. Go to **https://vercel.com** → sign in with GitHub
2. Click **Add New → Project**
3. Click **"Deploy a template"** — but actually we want to upload directly:
   - On the Import page, scroll down and choose **"Deploy without a Git
     repository"** — or just drag-drop the `landing/` folder
   - If you don't see that option: push the `landing/` folder to a new
     **public GitHub repo** (github.com → New repository → upload files) then
     import from there
4. Set **Root Directory** to `landing` if prompted
5. Click **Deploy** — done. Vercel gives you a URL like
   `https://jarvis-trading.vercel.app`
6. Optional: in Vercel dashboard → Settings → Domains → add your own domain

**Vercel Analytics (free, no code change needed for basic stats):**
- In your Vercel project → Analytics tab → Enable
- Vercel automatically injects the script. You'll see page views, countries,
  and referrers without touching the HTML.
- If you want the deeper event tracking (diagnostic completions, email
  signups), uncomment the GA4 block in index.html (see below).

---

## Step 3 — Set up Google Analytics 4 (optional but recommended)

For completion rate and signup rate you need GA4 custom events, which Vercel
Analytics doesn't capture.

1. Go to **https://analytics.google.com** → create a new property
2. Under "Data Streams" → Web → enter your Vercel URL → copy the **Measurement
   ID** (looks like `G-ABC123XYZ`)
3. In `index.html`, find these two commented blocks:
   ```html
   <!-- <script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXX">
   ```
   Uncomment both blocks (remove `<!--` and `-->`) and replace `G-XXXXXXXX`
   with your real ID
4. Redeploy (if using GitHub: commit and push; Vercel auto-deploys)

---

## Alternative: Netlify Drop (even simpler)

1. Go to **https://app.netlify.com/drop**
2. Drag the `landing/` folder onto the page
3. Done — you get a URL immediately, no account required
4. For analytics on Netlify: use GA4 above (Netlify Analytics costs $9/mo;
   GA4 is free and gives more detail)

---

## Numbers to watch

These are the only three numbers that tell you if there's real demand:

| Metric | Where to find it | What it means |
|--------|-----------------|---------------|
| **Completion rate** | GA4 → Events → `diagnostic_complete` ÷ total visitors | If > 60%, the hook is working — people care enough to finish. Below 30% means the landing copy needs work. |
| **Waitlist conversion** | GA4 → Events → `waitlist_signup` ÷ `diagnostic_complete` | If > 15% of people who see a result leave their email, the problem resonates. This is your demand signal. |
| **Share rate** | GA4 → Events → `click` on share buttons (auto-tracked) | Any organic sharing at all means the result hit a nerve. Even 5% share rate in a small audience is meaningful. |

**The demand threshold to proceed:**  
Run it for 2 weeks. If you get **≥ 50 email signups** from organic sharing
(not your own posts), the problem is real enough to build the backend.

**Where to post it first:**
- Korean crypto/futures communities: 코인판, DC인사이드 코인갤, 네이버 카페
- Twitter/X with the link — the shareable result URL is designed to spread
  ("My type: Discipline-deficient. What's yours?")
- Any Telegram group you're already in — share your own result with the link

---

## Quick sanity check before going live

Open `index.html` directly in a browser (just double-click the file) and:
- [ ] Click through all 5 questions
- [ ] Confirm you get a result card
- [ ] Try the language toggle — both KO and EN should work
- [ ] Enter a test email — after Formspree is set up you should see it in
      your Formspree dashboard
- [ ] Copy the share link and paste it in a new tab — the correct lang
      should load

That's it.
