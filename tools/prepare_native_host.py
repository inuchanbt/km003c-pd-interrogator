"""Prepare pinned sources and an isolated updater patch; never opens USB.

Use local Git checkouts, or explicitly --fetch public pinned GitHub archives.
Only .deps/native-host is written. Existing prepared output is not overwritten.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'km003c-rs': ('okhsunrog/km003c-rs', 'efed50556d7376914bffd510e012aaa98081936f'),
    'backup': ('inuchanbt/apdo-doom-on-km003c', '20622200644f02c6a5e48ad664dc7b8b6fb236ce'),
}


def edit(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Pinned source patch context mismatch')
    return text.replace(before, after, 1)


def patch_library(folder):
    path = folder / 'km003c-lib/src/firmware.rs'
    original = folder / 'km003c-lib/src/firmware.rs.upstream'
    if not original.exists():
        original.write_bytes(path.read_bytes())
    raw = original.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '8673828441c192397f7d2aa6163e81eecbd7a06606e9a9c711b8bcd0d38bec5a':
        raise ValueError('Unexpected pinned firmware.rs source hash')
    text = raw.decode()
    text = edit(text, 'mod install;', '''mod install;
#[cfg(feature = "hw23-overlay")]
pub mod hw23;

#[derive(Debug, Clone, Copy)]
enum ImageProfile {
    Standard,
    #[cfg(feature = "hw23-overlay")]
    Hw23Overlay,
}
impl ImageProfile {
    fn limit(self) -> usize {
        match self {
            Self::Standard => MAX_IMAGE_BYTES,
            #[cfg(feature = "hw23-overlay")]
            Self::Hw23Overlay => hw23::MAX_BYTES,
        }
    }
}''')
    text = edit(text, '    trailer: Vec<u8>,\n}', '    trailer: Vec<u8>,\n    profile: ImageProfile,\n}')
    text = edit(text, '    pub fn parse(bytes: &[u8]) -> Result<Self, KMError> {', '''    pub fn parse(bytes: &[u8]) -> Result<Self, KMError> {
        Self::parse_profile(bytes, ImageProfile::Standard)
    }

    fn parse_profile(bytes: &[u8], profile: ImageProfile) -> Result<Self, KMError> {''')
    text = edit(text, 'if size == 0 || size > MAX_IMAGE_BYTES {', 'if size == 0 || size > profile.limit() {')
    text = edit(text, '            trailer: trailer.to_vec(),', '            trailer: trailer.to_vec(),\n            profile,')
    text = edit(text, 'self.metadata.size > MAX_IMAGE_BYTES', 'self.metadata.size > self.profile.limit()')
    text = edit(text, '        validate_application_vectors(&self.data)?;', '        validate_application_vectors_with_limit(&self.data, self.profile.limit())?;')
    before = '''        Ok(())
    }

    fn setup_packet'''
    after = '''        #[cfg(feature = "hw23-overlay")]
        if matches!(self.profile, ImageProfile::Hw23Overlay) {
            hw23::validate_payload(&self.data)?;
            hw23::validate_ciphertext(self)?;
        }
        Ok(())
    }

    fn setup_packet'''
    text = edit(text, before, after)
    text = edit(text, '''fn validate_application_vectors(data: &[u8]) -> Result<(), KMError> {
    if !(8..=MAX_IMAGE_BYTES).contains(&data.len()) {''', '''fn validate_application_vectors(data: &[u8]) -> Result<(), KMError> {
    validate_application_vectors_with_limit(data, MAX_IMAGE_BYTES)
}

fn validate_application_vectors_with_limit(data: &[u8], limit: usize) -> Result<(), KMError> {
    if !(8..=limit).contains(&data.len()) {''')
    text = edit(text, '''        transfer_image(self, image, rand::random(), progress).await''', '''        #[cfg(feature = "hw23-overlay")]
        if matches!(image.profile, ImageProfile::Hw23Overlay) {
            hw23::verify_live_loader(self, &identity).await?;
        }
        transfer_image(self, image, rand::random(), progress).await''')
    path.write_text(text, encoding='utf-8', newline='\n')
    cargo = folder / 'km003c-lib/Cargo.toml'
    cargo_original = cargo.with_name('Cargo.toml.upstream')
    if not cargo_original.exists():
        cargo_original.write_bytes(cargo.read_bytes())
    text = edit(cargo_original.read_text(), 'default = []', 'default = []\n# Local exact-build extension profile; standard parsing remains unchanged.\nhw23-overlay = ["dep:sha2"]')
    cargo.write_text(text, encoding='utf-8', newline='\n')
    source = ROOT / 'native-host/library-patch/hw23.rs'
    (folder / 'km003c-lib/src/firmware/hw23.rs').write_bytes(source.read_bytes())
    workspace = folder / 'Cargo.toml'
    workspace_original = workspace.with_name('Cargo.toml.upstream')
    if not workspace_original.exists():
        workspace_original.write_bytes(workspace.read_bytes())
    # Unit-test the library without resolving unrelated GUI workspace members.
    text = edit(workspace_original.read_text(), '''members = [
    "km003c-lib",
    "km003c-cli",
    "km003c-egui",
    "realtime-plot"
]''', 'members = ["km003c-lib"]')
    workspace.write_text(text, encoding='utf-8', newline='\n')
    # Separate pinned workspace lock: the library's own unit tests resolve its
    # optional feature graph, whereas the host lock contains only selected deps.
    library_lock = ROOT / 'native-host/library-patch/Cargo.lock'
    if library_lock.exists():
        (folder / 'Cargo.lock').write_bytes(library_lock.read_bytes())


def extract(name, source, target, fetch):
    repo, revision = PINS[name]
    if source:
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', revision], text=True).strip()
        if actual != revision:
            raise ValueError('Pinned revision not found')
        command = ['git', '-C', str(source), 'archive', revision]
        if name == 'backup':
            command += ['bootstrap', 'LICENSE']
        archive = subprocess.check_output(command)
        strip = False
    elif fetch:
        with urllib.request.urlopen(f'https://codeload.github.com/{repo}/tar.gz/{revision}', timeout=60) as response:
            archive = response.read(64 * 1024 * 1024 + 1)
        strip = True
    else:
        raise ValueError('Supply both local source paths, or explicitly use --fetch')
    if len(archive) > 64 * 1024 * 1024:
        raise ValueError('Unexpected source archive size')
    target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:*') as tar:
        for member in tar:
            parts = Path(member.name).parts[1:] if strip else Path(member.name).parts
            if not parts:
                continue
            if name == 'backup' and parts[0] not in ('bootstrap', 'LICENSE'):
                continue
            dest = target.joinpath(*parts)
            if not dest.resolve().is_relative_to(target.resolve()):
                raise ValueError('Archive path escapes the prepared dependency')
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as stream:
                    dest.write_bytes(stream.read())
            else:
                raise ValueError('Source archive contains an unsupported special file')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--km003c-source', type=Path)
    parser.add_argument('--backup-source', type=Path)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--refresh-patch', action='store_true', help='Refresh only the isolated local patch from preserved pinned source')
    args = parser.parse_args()
    folder = ROOT / '.deps/native-host'
    if args.refresh_patch:
        report = json.loads((folder / 'prepared.json').read_text())
        if any(report['sources'][name]['revision'] != pin[1] for name, pin in PINS.items()):
            parser.error('Prepared sources do not match pins')
        patch_library(folder / 'km003c-rs')
        report['patch_sha256'] = hashlib.sha256((ROOT / 'native-host/library-patch/hw23.rs').read_bytes()).hexdigest()
        (folder / 'prepared.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Isolated patch refreshed. No source checkout or USB accessed.')
        return
    if folder.exists():
        parser.error('Prepared directory exists; preserve it and inspect the recorded manifest')
    folder.mkdir(parents=True)
    extract('km003c-rs', args.km003c_source, folder / 'km003c-rs', args.fetch)
    extract('backup', args.backup_source, folder / 'backup', args.fetch)
    patch_library(folder / 'km003c-rs')
    report = dict(schema=1, sources={name: dict(repository=repo, revision=rev) for name, (repo, rev) in PINS.items()},
                  patch_sha256=hashlib.sha256((ROOT / 'native-host/library-patch/hw23.rs').read_bytes()).hexdigest(),
                  usb_access=False)
    (folder / 'prepared.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Pinned sources prepared; standard updater limits preserved. No USB accessed.')


if __name__ == '__main__':
    main()
