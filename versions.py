"""Media versions: keep the previous bytes of a referenced media file alive so the editor can show before / after.

Idea: a HARDLINK of the current file is kept in `<project>/.kurgu/versions/<stem>~<h6>@<mtime_ns><ext>` (h6 = short hash of the
real path, so two `clip.mp4` in different folders never clash). A re-render writes a temp file and renames it over the target
(atomic), which gives the path a NEW inode; the hardlink still holds the OLD one, so the previous content survives for free.
An in-place overwrite (same inode rewritten) cannot be saved this way: the old bytes are gone, and the link would show the new
bytes, so such a version is dropped. Where hardlinks are impossible (other filesystem, FAT) a copy is made; files over
MAX_COPY_BYTES are skipped (a note goes to stderr). The last KEEP versions per file are kept (the current one included).

No server needed: the `rerender` command can call snapshot() right before it replaces a file.
"""
import hashlib
import os
import kurgu_paths
import shutil
import sys

KEEP = 3
MAX_COPY_BYTES = 200 << 20       # ~200 MB: bigger files are not copied (hardlinks cost nothing, so they are always taken)
_noted = set()


def versions_dir(project_dir):
    """.kurgu/versions, refused (KurguRefusal, an OSError) when .kurgu or the folder is a symlink / junction: media must not be hardlinked or copied through it."""
    import agent_notes
    for x in (os.path.join(project_dir, ".kurgu"), os.path.join(project_dir, ".kurgu", "versions")):
        if agent_notes._linkish(x):
            raise agent_notes.KurguRefusal("symlink", f"refusing to write: {x} is a symbolic link", path=os.path.relpath(x, project_dir))
    return os.path.join(project_dir, ".kurgu", "versions")


def _prefix(path):
    real = os.path.realpath(path)
    stem, _ = os.path.splitext(os.path.basename(real))
    return f"{stem}~{hashlib.sha1(real.encode('utf-8', 'surrogateescape')).hexdigest()[:6]}@"


def _name(path, mtime_ns):
    return f"{_prefix(path)}{mtime_ns}{os.path.splitext(path)[1]}"


def _note(key, msg):
    if key not in _noted:
        _noted.add(key)
        print(f"kurgu: {msg}", file=sys.stderr)


def _copy_version(path, target, st, vd, project_dir):
    """Copy `path` to `target` (as `.part` first). Dropped when the file changed while it was being copied."""
    tmp = kurgu_paths.tmp_path(target, ".part")
    try:
        shutil.copy2(path, tmp)
        now = os.stat(path)
        if (now.st_mtime_ns, now.st_size) != (st.st_mtime_ns, st.st_size):         # replaced meanwhile: this is not the version we meant to keep
            os.remove(tmp)
            return
        os.replace(tmp, target)
        prune(project_dir, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def snapshot(project_dir, path, st=None, sync=False):
    """Keep the current content of `path` as a version; returns the version file's path (None: skipped / failed / copy still running). Idempotent.
    A hardlink costs nothing and is always taken. Where links are impossible a COPY is made: never in the caller's thread unless `sync=True`
    (`kurgu rerender` needs the old bytes before it replaces the file; the editor's request handler must never wait on a copy), and never above MAX_COPY_BYTES."""
    try:
        if os.path.islink(path):                                  # a symlinked media file would put what it points to into the project's folder
            return None
        path = os.path.realpath(path)
        st = st or os.stat(path)
        vd = versions_dir(project_dir)
        os.makedirs(vd, exist_ok=True)
        target = os.path.join(vd, _name(path, st.st_mtime_ns))
        if not os.path.exists(target):
            try:
                os.link(path, target)
            except OSError:
                if st.st_size > MAX_COPY_BYTES:
                    _note(path, f"{os.path.basename(path)} is over {MAX_COPY_BYTES >> 20} MB and cannot be hard-linked here: no before/after version kept for it")
                    return None
                if not sync:
                    import threading
                    key = ("copy", target)
                    if key not in _noted:
                        _noted.add(key)
                        threading.Thread(target=lambda: (_copy_version(path, target, st, vd, project_dir), _noted.discard(key)), daemon=True).start()
                    return None
                _copy_version(path, target, st, vd, project_dir)
                if not os.path.exists(target):
                    return None
            real_mtime = os.stat(target).st_mtime_ns        # the file may have been swapped between stat and link: name it by what it is
            if real_mtime != st.st_mtime_ns:
                fixed = os.path.join(vd, _name(path, real_mtime))
                os.replace(target, fixed)
                target = fixed
        _drop_overwritten(path, target, st)
        prune(project_dir, path)
        return target
    except OSError:
        return None


def _drop_overwritten(path, keep, cur):
    """A version that shares the file's inode with the current content is not a previous version (in-place overwrite): remove it."""
    for v in _files(os.path.dirname(keep), path):
        if v != keep:
            try:
                if os.path.samestat(os.stat(v), cur):
                    os.remove(v)
            except OSError:
                pass


def _files(vd, path):
    pre = _prefix(path)
    try:
        names = os.listdir(vd)
    except OSError:
        return []
    out = []
    for n in names:
        if n.startswith(pre) and not n.endswith(".part"):
            stem = os.path.splitext(n[len(pre):])[0]
            if stem.isdigit():
                out.append((int(stem), os.path.join(vd, n)))
    out.sort(reverse=True)
    return [p for _, p in out]


def prune(project_dir, path, keep=KEEP):
    for old in _files(versions_dir(project_dir), path)[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def list_versions(project_dir, path):
    """Newest first: [{"mtime_ns", "size", "file", "current"}]; `current` is the version whose content is the file's present one."""
    try:
        cur = os.stat(os.path.realpath(path))
    except OSError:
        cur = None
    out = []
    try:
        vdir = versions_dir(project_dir)
    except OSError:
        return out
    for f in _files(vdir, path):
        try:
            st = os.stat(f)
        except OSError:
            continue
        out.append({"mtime_ns": st.st_mtime_ns, "size": st.st_size, "file": f,
                    "current": bool(cur and st.st_mtime_ns == cur.st_mtime_ns and st.st_size == cur.st_size)})
    return out
