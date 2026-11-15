# Adding a project and publishing to GitHub

## 0. One-time setup (Mac)

1. Install Apple's command line tools (provides `git` and `python3`):
   ```bash
   xcode-select --install
   ```
2. Install the image library used by the script (from the repo folder):
   ```bash
   cd ~/Documents/DeepikaWork/projects/annotation-portfolio
   python3 -m pip install --user -r tools/requirements.txt
   ```
3. Tell git who you are (the name/email shown on your commits):
   ```bash
   git config --global user.name  "Your Name"
   git config --global user.email "you@example.com"
   ```

## 1. Export from CVAT

1. Open the **task** (or project) in CVAT → **Actions ▸ Export task dataset**.
2. Format:
   - image tasks (boxes, polygons, masks, skeletons): **CVAT for images 1.1**
   - video / tracking tasks: **CVAT for video 1.1**
3. Tick **Save images**, so the script can draw previews.
4. Download the `.zip`.

## 2. Put it in the project folder

Unzip it into the matching `projects/<project>/export/` folder so you get:

```
projects/01-object-detection-bboxes/export/annotations.xml
projects/01-object-detection-bboxes/export/images/...
```

(You can also drop the `.zip` itself into `export/`; the script unzips it.)

To add a fifth project, copy one of the project folders, rename it
(e.g. `05-image-classification-tags`), empty its `export/` and `previews/`, and edit its README.

## 3. Generate previews and statistics

```bash
python3 tools/cvat_showcase.py --all
# or one project, with 4 samples:
python3 tools/cvat_showcase.py projects/04-video-object-tracking --samples 4
```

This writes `previews/sample_*.jpg` (plus `previews/tracking.gif` for video), `STATS.md`,
and fills the `STATS` and `PREVIEWS` sections of the project README.
Then update the **Scale** column in the top-level README with the numbers from `STATS.md`.

> By default, `.gitignore` keeps `export/images/` out of the repo (only `annotations.xml` and
> previews are published). Remove that line from `.gitignore` to publish the full image set.

## 4. Publish to GitHub (first time)

The git repository is the whole `projects` folder (this portfolio is one folder inside it).

1. On github.com → **New repository** → name `projects`, **Public**, no README.
2. In Terminal:
   ```bash
   cd ~/Documents/DeepikaWork/projects
   git commit -m "Add annotation portfolio"
   git remote add origin https://github.com/deepikadhineshkrishnan/projects.git
   git push -u origin main
   ```
   When asked for a password, use a **personal access token**
   (GitHub → Settings → Developer settings → Personal access tokens), or use GitHub Desktop.

## 5. Later updates

```bash
cd ~/Documents/DeepikaWork/projects/annotation-portfolio
python3 tools/cvat_showcase.py --all
cd ..
git add .
git commit -m "Add <project name>"
git push
```

## 6. Make it easy to find

- On the repo page, click ⚙️ next to **About** and add a description and topics:
  `data-annotation`, `cvat`, `computer-vision`, `image-labeling`, `portfolio`.
- **Pin** the repo on your GitHub profile (Profile → Customize your pins).
- Optional profile page: create a repo named exactly your username with a short README
  (who you are + link to this portfolio). GitHub shows it at the top of your profile.
- Put the repo link on your CV, LinkedIn and freelance profiles.

## Checklist before going public

- [ ] All images and labels are yours to share (no client or employer data, no faces or
      licence plates of people who haven't agreed, unless the source dataset allows it)
- [ ] Every `CAPITALS` placeholder and example row replaced
- [ ] Previews look right (labels on the right objects, nothing private visible)
- [ ] Repo size reasonable (`du -sh .git` under a few hundred MB)
