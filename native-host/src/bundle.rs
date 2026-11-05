// SPDX-License-Identifier: MIT
use anyhow::{Context, Result, ensure};
use km003c_backup as backup;
use km003c_lib::firmware::{FirmwareImage, hw23};
use serde_json::json;
use std::{fs, path::Path};

pub const BASE: usize = 0x4000;
pub const END: usize = 0x80000;
pub const WRITE_END: usize = 0x78000;
const STOCK_HASH: &str = "c63057eaf08b8625e7ac2fc7270c06d75515e78fdbdd46080bdf4b696984ec67";

pub struct Stock {
    pub manifest: backup::Manifest,
    pub loader: Vec<u8>,
    pub original: Vec<u8>,
    pub baseline: Vec<u8>, // Internal flash 0x4000..0x80000, no loader/ICG/OTP.
    pub restore_package: Vec<u8>,
    pub calibration: Vec<u8>,
}
pub struct Bundle {
    pub overlay: FirmwareImage,
    pub erase_pass: FirmwareImage,
    pub restore: FirmwareImage,
    pub overlay_state: Vec<u8>,
    pub erase_state: Vec<u8>,
}

pub fn stock(folder: &Path) -> Result<Stock> {
    let manifest = backup::verify_qualified(folder)?;
    ensure!(
        manifest.identity.model == "KM003C"
            && manifest.identity.hardware == "2.3"
            && manifest.identity.firmware == "2.0.7",
        "Only the qualified HW2.3/stock-2.0.7 backup is supported"
    );
    let original = fs::read(folder.join(backup::APP))?;
    ensure!(
        backup::digest(&original) == STOCK_HASH,
        "Unrecognized original application"
    );
    let loader = fs::read(folder.join(backup::LOADER))?;
    hw23::verify_loader(&loader)?;
    let tail = fs::read(folder.join("internal-tail-00070000.bin"))?;
    ensure!(
        tail.len() == 0x10000
            && original.len() == 0x738c0 - BASE
            && original[0x70000 - BASE..] == tail[..original.len() - (0x70000 - BASE)],
        "Tail overlap differs from stock"
    );
    ensure!(
        tail[0x38c0..].iter().all(|b| *b == 255),
        "Proposed internal extension region was not erased"
    );
    let mut baseline = vec![255; END - BASE];
    baseline[..original.len()].copy_from_slice(&original);
    baseline[0x70000 - BASE..].copy_from_slice(&tail);
    let restore_package = fs::read(folder.join("stock-restore.mencrypt"))?;
    let calibration = fs::read(folder.join("calibration-03000c00.bin"))?;
    ensure!(
        calibration.len() == 1024,
        "Unexpected qualified calibration region"
    );
    let restore = FirmwareImage::parse(&restore_package)?;
    ensure!(
        restore.installed_data()? == original,
        "Qualified restore container does not reproduce original bytes"
    );
    Ok(Stock {
        manifest,
        loader,
        original,
        baseline,
        restore_package,
        calibration,
    })
}

/// Model only erase extent/programmed bytes/metadata. No peripheral timing.
pub fn apply_installed(current: &[u8], installed: &[u8]) -> Result<Vec<u8>> {
    ensure!(
        current.len() == END - BASE && (0x460..=hw23::MAX_BYTES).contains(&installed.len()),
        "Invalid modeled flash range"
    );
    let erase_bytes = installed.len().div_ceil(0x2000) * 0x2000;
    ensure!(
        BASE + erase_bytes <= WRITE_END,
        "Erase exceeds the two-extra-sector profile"
    );
    let mut result = current.to_vec();
    result[..erase_bytes].fill(255);
    result[..installed.len()].copy_from_slice(installed);
    Ok(result)
}

fn package_record(name: &str, data: &[u8]) -> serde_json::Value {
    json!({"file":name,"bytes":data.len(),"sha256":backup::digest(data)})
}

pub fn package(stock_path: &Path, candidate: &Path, output: &Path) -> Result<()> {
    let s = stock(stock_path)?;
    ensure!(!output.exists(), "Output must be a new private directory");
    let overlay = hw23::package(
        &fs::read(candidate.join("candidate-uncommitted.bin"))?,
        &s.loader,
    )?;
    let erase = hw23::package(
        &fs::read(candidate.join("rollback-erase-pass-uncommitted.bin"))?,
        &s.loader,
    )?;
    fs::create_dir(output).context("Create new private package directory")?;
    for (name, data) in [
        ("overlay.mencrypt", &overlay),
        ("rollback-erase.mencrypt", &erase),
        ("stock-restore.mencrypt", &s.restore_package),
    ] {
        backup::save(&output.join(name), data)?;
    }
    let manifest = json!({"schema":1,"purpose":"exact HW2.3 offline GUI call-through probe",
        "stock_manifest_sha256":backup::digest(&fs::read(stock_path.join("manifest.json"))?),
        "stock_sha256":STOCK_HASH,"loader_sha256":hw23::LOADER_HASH,
        "application_base":BASE,"maximum_erase_end":WRITE_END,
        "files":[package_record("overlay.mencrypt",&overlay),package_record("rollback-erase.mencrypt",&erase),
                 package_record("stock-restore.mencrypt",&s.restore_package)]});
    backup::save_json(&output.join("bundle.json"), &manifest)?;
    let b = audit(&s, stock_path, output)?;
    backup::save(
        &output.join("expected-overlay-internal.bin"),
        &b.overlay_state,
    )?;
    backup::save(
        &output.join("expected-erase-pass-internal.bin"),
        &b.erase_state,
    )?;
    backup::save_json(
        &output.join("offline-verified.json"),
        &json!({
        "schema":1,"usb_access":false,"package_roundtrip_verified":true,"modeled_rollback_exact":true,
        "overlay_bytes":b.overlay.data().len(),"erase_end":WRITE_END,
        "standard_parser_rejects_extended":FirmwareImage::parse(&overlay).is_err()}),
    )?;
    println!(
        "Created private bundle; package/readback roundtrip and exact modeled rollback passed. No USB accessed."
    );
    Ok(())
}

pub fn audit(s: &Stock, stock_path: &Path, folder: &Path) -> Result<Bundle> {
    let manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(folder.join("bundle.json"))?)?;
    ensure!(
        manifest["schema"] == 1
            && manifest["stock_sha256"] == STOCK_HASH
            && manifest["loader_sha256"] == hw23::LOADER_HASH
            && manifest["application_base"] == BASE
            && matches!(
                manifest["maximum_erase_end"].as_u64(),
                Some(0x76000 | 0x78000)
            )
            && manifest["stock_manifest_sha256"]
                == backup::digest(&fs::read(stock_path.join("manifest.json"))?),
        "Bundle does not belong to this qualified backup/profile"
    );
    let names = [
        "overlay.mencrypt",
        "rollback-erase.mencrypt",
        "stock-restore.mencrypt",
    ];
    let records = manifest["files"]
        .as_array()
        .context("Missing bundle files")?;
    ensure!(records.len() == 3, "Unexpected bundle file list");
    let mut encoded = Vec::new();
    for (name, record) in names.iter().zip(records) {
        ensure!(record["file"] == *name, "Unexpected bundle path");
        let data = fs::read(folder.join(name))?;
        ensure!(
            record["bytes"] == data.len() && record["sha256"] == backup::digest(&data),
            "Bundle file changed: {name}"
        );
        encoded.push(data);
    }
    ensure!(
        FirmwareImage::parse(&encoded[0]).is_err() && FirmwareImage::parse(&encoded[1]).is_err(),
        "Extended images unexpectedly entered the ordinary update path"
    );
    let overlay = hw23::parse(&encoded[0], &s.loader)?;
    let erase_pass = hw23::parse(&encoded[1], &s.loader)?;
    let restore = FirmwareImage::parse(&encoded[2])?;
    let declared_end = manifest["maximum_erase_end"].as_u64().unwrap() as usize;
    ensure!(
        BASE + overlay.data().len().div_ceil(0x2000) * 0x2000 <= declared_end,
        "Bundle exceeds its declared erase boundary"
    );
    ensure!(
        hw23::validate_payload(overlay.data())? == hw23::Kind::Overlay
            && hw23::validate_payload(erase_pass.data())? == hw23::Kind::RollbackErasePass,
        "Overlay and rollback package roles differ"
    );
    ensure!(
        overlay.data().len() == erase_pass.data().len(),
        "Rollback erase span differs from overlay"
    );
    ensure!(
        restore.installed_data()? == s.original && encoded[2] == s.restore_package,
        "Original restore package changed"
    );
    let overlay_state = apply_installed(&s.baseline, &overlay.installed_data()?)?;
    let erase_state = apply_installed(&overlay_state, &erase_pass.installed_data()?)?;
    let restored = apply_installed(&erase_state, &restore.installed_data()?)?;
    ensure!(
        restored == s.baseline,
        "Modeled two-stage rollback differs from full original internal region"
    );
    ensure!(
        apply_installed(&overlay_state, &restore.installed_data()?)? != s.baseline,
        "Profile no longer demonstrates why the extension erase pass is required"
    );
    Ok(Bundle {
        overlay,
        erase_pass,
        restore,
        overlay_state,
        erase_state,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn direct_stock_restore_leaves_extension_but_two_passes_restore_every_byte() {
        let mut baseline = vec![255; END - BASE];
        let stock = vec![0x5a; 0x738c0 - BASE];
        baseline[..stock.len()].copy_from_slice(&stock);
        let mut overlay = stock.clone();
        overlay.resize(0x72400, 255);
        overlay[0x70000..].fill(0x12);
        let changed = apply_installed(&baseline, &overlay).unwrap();
        assert_ne!(apply_installed(&changed, &stock).unwrap(), baseline);
        let mut pass = stock.clone();
        pass.resize(overlay.len(), 255);
        let erased = apply_installed(&changed, &pass).unwrap();
        assert_eq!(apply_installed(&erased, &stock).unwrap(), baseline);
        assert_eq!(changed[WRITE_END - BASE..], baseline[WRITE_END - BASE..]);
    }

    #[test]
    fn erase_bounds_and_neighbor_changes_are_detectable() {
        let baseline = vec![255; END - BASE];
        assert!(apply_installed(&baseline, &vec![0; hw23::MAX_BYTES + 1]).is_err());
        assert!(apply_installed(&baseline, &vec![0; 0x45f]).is_err());
        let result = apply_installed(&baseline, &vec![0x12; 0x72400]).unwrap();
        assert_eq!(
            result[0x72400..WRITE_END - BASE],
            vec![255; WRITE_END - BASE - 0x72400]
        );
        let mut corrupted = result.clone();
        corrupted[WRITE_END - BASE] ^= 1;
        assert_ne!(corrupted[WRITE_END - BASE..], baseline[WRITE_END - BASE..]);
    }
}
