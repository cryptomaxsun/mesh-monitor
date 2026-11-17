# Инструкция: как загрузить Mesh Monitor на GitHub

Все файлы уже очищены от паролей, токенов, координат и личных адресов и сложены в папку **`F:\MeshMonitor-GitHub`**.  
Ниже — два простых способа выложить их на GitHub.

---

## Способ 1: Через командную строку (Git) — самый быстрый

### Шаг 1. Создай пустой репозиторий на GitHub
1. Открой https://github.com/new (войди в свой аккаунт).
2. В поле **Repository name** напиши, например: `mesh-monitor`.
3. Выбери **Public** (или **Private**, если хочешь закрытый репозиторий).
4. **Не ставь** галочки «Add a README file», «Add .gitignore» или «Choose a license» — они у нас уже есть в папке.
5. Нажми **Create repository**.
6. Скопируй HTTPS-ссылку на репозиторий (вида `https://github.com/ТВОЙ_ЛОГИН/mesh-monitor.git`).

### Шаг 2. Инициализируй Git и отправь код
Открой PowerShell (или командную строку) и выполни по очереди:

```powershell
cd "F:\MeshMonitor-GitHub"
git init
git branch -M main
git add .
git status
```
> Проверь в `git status`, что в списке нет файлов `.keystore` и `.apk` (они скрыты через `.gitignore`).

Затем сделай первый коммит и отправь на GitHub:

```powershell
git commit -m "Initial commit: Mesh Monitor (Raspberry Pi + Android + ESP32)"
git remote add origin https://github.com/ТВОЙ_ЛОГИН/mesh-monitor.git
git push -u origin main
```
Если Git попросит авторизоваться — войди через браузер в появившемся окне Git Credential Manager.

---

## Способ 2: Через GitHub Desktop (без команд)
1. Скачай и установи [GitHub Desktop](https://desktop.github.com/), войди в свой аккаунт.
2. Нажми **File → Add local repository…** и выбери папку `F:\MeshMonitor-GitHub`.
3. Нажми **create a repository** (если предложит инициализировать Git) → **Create Repository**.
4. Внизу слева введи название коммита (например, `Initial commit`) и нажми **Commit to main**.
5. Наверху нажми **Publish repository**, выбери имя `mesh-monitor` и (по желанию) сними галочку «Keep this code private», чтобы сделать репозиторий публичным.

---

## (По желанию) Как прикрепить готовый `MeshMonitor.apk` в Releases
Чтобы пользователи могли скачать готовый APK, не собирая его вручную:
1. На странице твоего репозитория на GitHub справа нажми **Releases → Create a new release**.
2. В поле **Tag** напиши `v4.3`, в **Release title** — `Mesh Monitor v4.3`.
3. Перетащи файл `F:\Mesh monitor\MeshMonitor.apk` в область **Attach binaries by dropping them here**.
4. Нажми **Publish release**.

---

## Как обновлять репозиторий в будущем
Когда скопируешь новые версии файлов в `F:\MeshMonitor-GitHub` (не забывая про плейсхолдеры вместо паролей):
```powershell
cd "F:\MeshMonitor-GitHub"
git add .
git commit -m "Описание изменений"
git push
```
