// SPDX-License-Identifier: MIT
//! Qualified live access. Writes use only the pinned library updater.
use crate::bundle::{self, Bundle, Stock};
use anyhow::{Context, Result, ensure};
use km003c_backup::{self as backup, ReadMemory};
use km003c_lib::{
    DeviceConfig, DeviceSelector, KM003C,
    firmware::{FirmwareImage, FirmwareUsbMode, find_firmware_device, hw23},
};
use serde_json::json;
use std::{fs, path::Path, time::Duration};

struct Session {
    reader: backup::Reader,
    mode: FirmwareUsbMode,
}
struct Snapshot {
    internal: Vec<u8>,
}

fn selector(s: &Stock) -> DeviceSelector {
    DeviceSelector::PortPath {
        bus_id: s.manifest.usb_port.bus_id.clone(),
        port_chain: s.manifest.usb_port.port_chain.clone(),
    }
}

async fn open(s: &Stock, require_dfu: bool) -> Result<Session> {
    let selected = selector(s);
    let found = find_firmware_device(&selected).await?;
    let dfu = found.mode == FirmwareUsbMode::Dfu;
    ensure!(
        dfu || (!require_dfu
            && found.mode == FirmwareUsbMode::Application
            && found.product_string.as_deref() == Some("POWER-Z KM003C")),
        "Manually enter native DFU on the qualified USB port; keep only side HID connected"
    );
    let mut device = if dfu {
        KM003C::open_maintenance(selected).await?
    } else {
        KM003C::new(DeviceConfig::vendor().skip_reset().select(selected)).await?
    };
    let identity = if dfu {
        device.get_bootloader_info().await?
    } else {
        device.get_device_info().await?
    };
    ensure!(
        identity.model == "KM003C" && identity.hw_version == "2.3",
        "Live device is not KM003C HW2.3"
    );
    if !identity.serial_id.is_empty() {
        ensure!(
            identity.serial_id == s.manifest.identity.serial,
            "Device serial differs from qualified backup"
        );
    }
    let mut reader = backup::Reader { device, dfu };
    let loader = reader.read(4, 0x3ffc).await?;
    ensure!(
        loader == s.loader,
        "Live loader differs from the qualified baseline"
    );
    hw23::verify_loader(&loader)?;
    Ok(Session {
        reader,
        mode: found.mode,
    })
}

fn new_evidence(path: &Path) -> Result<()> {
    fs::create_dir(path).context("Evidence directory must be new, with an existing parent")
}

async fn snapshot(
    session: &mut Session,
    s: &Stock,
    output: &Path,
    label: &str,
) -> Result<Snapshot> {
    let loader = session.reader.read(4, 0x3ffc).await?;
    backup::save(&output.join(format!("{label}-loader.bin")), &loader)?;
    ensure!(
        loader == s.loader,
        "Loader changed; do not start application"
    );
    let internal = session
        .reader
        .read(bundle::BASE as u32, bundle::END - bundle::BASE)
        .await?;
    backup::save(&output.join(format!("{label}-internal.bin")), &internal)?;
    ensure!(
        internal[bundle::WRITE_END - bundle::BASE..]
            == s.baseline[bundle::WRITE_END - bundle::BASE..],
        "Internal flash outside the permitted erase span differs; no write/start allowed"
    );
    if !session.reader.dfu {
        let calibration = session.reader.read(0x03000c00, 1024).await?;
        backup::save(
            &output.join(format!("{label}-calibration.bin")),
            &calibration,
        )?;
        ensure!(
            calibration == s.calibration,
            "Calibration differs from this device's qualified backup"
        );
    }
    Ok(Snapshot { internal })
}

fn state(data: &[u8], s: &Stock, b: &Bundle) -> &'static str {
    if data == s.baseline {
        "original-stock"
    } else if data == b.overlay_state {
        "overlay-probe"
    } else if data == b.erase_state {
        "rollback-erase-pass"
    } else {
        "unknown-within-write-span"
    }
}

pub async fn check(s: &Stock, b: &Bundle, output: &Path) -> Result<()> {
    new_evidence(output)?;
    let mut session = open(s, false).await?;
    let before = snapshot(&mut session, s, output, "read-only").await?;
    let label = state(&before.internal, s, b);
    backup::save_json(
        &output.join("read-only.json"),
        &json!({"schema":1,"mode":format!("{:?}",session.mode),
        "state":label,"loader_matches":true,"protected_neighbor_matches":true,"flash_writes":0,"reboot_sent":false,
        "calibration_matches":if session.reader.dfu {None} else {Some(true)},
        "internal_sha256":backup::digest(&before.internal)}),
    )?;
    ensure!(
        label != "unknown-within-write-span",
        "Unknown current internal bytes; evidence saved, no writes made"
    );
    println!("Read-only device check passed: {label}. No flash, PD or reboot command sent.");
    Ok(())
}

pub async fn inspect_gui(s: &Stock, b: &Bundle, output: &Path) -> Result<()> {
    new_evidence(output)?;
    let mut session = open(s, false).await?;
    ensure!(
        session.mode == FirmwareUsbMode::Application,
        "GUI snapshot requires the known application, not DFU"
    );
    let before = snapshot(&mut session, s, output, "before-gui").await?;
    ensure!(
        state(&before.internal, s, b) != "unknown-within-write-span",
        "Unknown application; no GUI interpretation allowed"
    );
    let ram = session.reader.read(0x1fff8000, 0x2f000).await?;
    backup::save(&output.join("sram.bin"), &ram)?;
    let at = (0x2001a8d0u32 - 0x1fff8000) as usize;
    let pool = u32::from_le_bytes(ram[at..at + 4].try_into()?);
    ensure!(
        (0x1fff8000..=0x2001f000).contains(&pool),
        "LVGL heap pointer outside supported SRAM"
    );
    let heap = session.reader.read(pool, 0x8000).await?;
    backup::save(&output.join("lvgl-heap.bin"), &heap)?;
    backup::save_json(
        &output.join("gui-snapshot.json"),
        &json!({"schema":1,"read_only":true,
        "state":state(&before.internal,s,b),"sram_base":0x1fff8000u32,"sram_bytes":ram.len(),
        "lvgl_heap_base":pool,"lvgl_heap_bytes":heap.len(),"atomic_snapshot":false,
        "private_ram_must_not_be_published":true}),
    )?;
    println!("Private GUI SRAM/heap snapshot saved; no flash, mode switch or PD command sent.");
    Ok(())
}

pub async fn enter_dfu(s: &Stock, b: &Bundle, output: &Path) -> Result<()> {
    new_evidence(output)?;
    let mut session = open(s, false).await?;
    let before = snapshot(&mut session, s, output, "before-dfu").await?;
    ensure!(
        state(&before.internal, s, b) != "unknown-within-write-span",
        "Only a verified known application may request software DFU; otherwise use physical Exit recovery"
    );
    if session.mode == FirmwareUsbMode::Dfu {
        backup::save_json(
            &output.join("dfu-verified.json"),
            &json!({"already_in_dfu":true,"flash_writes":0}),
        )?;
        return Ok(());
    }
    backup::save_json(
        &output.join("before-dfu-verified.json"),
        &json!({"state":state(&before.internal,s,b),"flash_writes":0}),
    )?;
    let result = session.reader.device.jump_to_dfu().await;
    drop(session);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
    while tokio::time::Instant::now() < deadline {
        if let Ok(found) = find_firmware_device(&selector(s)).await {
            if found.mode == FirmwareUsbMode::Dfu {
                let mut dfu = open(s, true).await?;
                let after = snapshot(&mut dfu, s, output, "after-dfu").await?;
                ensure!(
                    after.internal == before.internal,
                    "Internal flash changed on entry to DFU"
                );
                backup::save_json(
                    &output.join("dfu-verified.json"),
                    &json!({"schema":1,
                    "loader_matches":true,"full_internal_unchanged":true,"flash_writes":0}),
                )?;
                println!(
                    "Native DFU entered and full internal region unchanged. No flash write made."
                );
                return Ok(());
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    anyhow::bail!("DFU did not re-enumerate; no retry issued. Jump result: {result:?}")
}

async fn write_verified(
    session: &mut Session,
    s: &Stock,
    image: &FirmwareImage,
    expected: &[u8],
    output: &Path,
    label: &str,
) -> Result<()> {
    // Save the exact target before any erase. Storage failures stop here.
    backup::save(&output.join(format!("{label}-expected.bin")), expected)?;
    backup::save_json(
        &output.join(format!("{label}-begin.json")),
        &json!({
        "schema":1,"raw_image_sha256":backup::digest(image.data()),"raw_image_bytes":image.data().len(),
        "erase_end":bundle::BASE+image.data().len().div_ceil(0x2000)*0x2000}),
    )?;
    let result = session
        .reader
        .device
        .update_firmware(image, |progress| {
            eprintln!(
                "{:?}: {}/{}",
                progress.stage, progress.bytes_written, progress.total_bytes
            );
        })
        .await;
    if let Err(error) = result {
        // Do not send further queries: the native loader may still expect raw
        // ciphertext. Preserve evidence, disconnect and re-enter DFU manually.
        let _ = backup::save_json(
            &output.join(format!("{label}-failed.json")),
            &json!({
            "error":error.to_string(),"retried":false,"further_usb_commands_sent":false}),
        );
        return Err(error.into());
    }
    let actual = snapshot(session, s, output, label).await?;
    ensure!(
        actual.internal == expected,
        "Full internal readback differs; remaining in DFU without retry"
    );
    backup::save_json(
        &output.join(format!("{label}-verified.json")),
        &json!({
        "schema":1,"loader_unchanged":true,"full_internal_readback":true,"protected_neighbor_matches":true,
        "internal_sha256":backup::digest(&actual.internal),"remaining_in_dfu":true}),
    )?;
    Ok(())
}

fn flash_source_allowed(current: &[u8], original: &[u8], previous: Option<&[u8]>) -> bool {
    current == original || previous.is_some_and(|expected| current == expected)
}

pub async fn flash(s: &Stock, b: &Bundle, previous: Option<&Bundle>, output: &Path) -> Result<()> {
    new_evidence(output)?;
    let mut session = open(s, true).await?;
    let before = snapshot(&mut session, s, output, "before").await?;
    if before.internal == b.overlay_state {
        backup::save_json(
            &output.join("already-installed.json"),
            &json!({"verified":true,"writes":0}),
        )?;
        println!(
            "Exact probe is already installed; no flash write repeated. Device remains in DFU."
        );
        return Ok(());
    }
    ensure!(
        flash_source_allowed(
            &before.internal,
            &s.baseline,
            previous.map(|bundle| bundle.overlay_state.as_slice())
        ),
        "Flash probe requires exact stock or explicitly audited previous probe; use restore for recovery"
    );
    backup::save_json(
        &output.join("flash-source-verified.json"),
        &json!({"full_internal_matches":true,
        "source":if before.internal == s.baseline {"original"} else {"audited-previous-probe"}}),
    )?;
    write_verified(
        &mut session,
        s,
        &b.overlay,
        &b.overlay_state,
        output,
        "probe",
    )
    .await?;
    println!("Probe and full internal region verified; loader unchanged. Device remains in DFU.");
    Ok(())
}

#[derive(Debug, PartialEq, Eq)]
enum RestoreStep {
    ErasePass,
    Original,
}
fn restore_plan(current: &[u8], original: &[u8], erased: &[u8]) -> Vec<RestoreStep> {
    if current == original {
        vec![]
    } else if current == erased {
        vec![RestoreStep::Original]
    } else {
        vec![RestoreStep::ErasePass, RestoreStep::Original]
    }
}

pub async fn restore(s: &Stock, b: &Bundle, output: &Path) -> Result<()> {
    new_evidence(output)?;
    let mut session = open(s, true).await?;
    let before = snapshot(&mut session, s, output, "before").await?;
    // Explicit recovery may replace unknown bytes INSIDE the permitted span.
    // Loader and all bytes outside that span must still match the baseline.
    for step in restore_plan(&before.internal, &s.baseline, &b.erase_state) {
        match step {
            RestoreStep::ErasePass => {
                write_verified(
                    &mut session,
                    s,
                    &b.erase_pass,
                    &b.erase_state,
                    output,
                    "erase-pass",
                )
                .await?
            }
            RestoreStep::Original => {
                write_verified(&mut session, s, &b.restore, &s.baseline, output, "original").await?
            }
        }
    }
    backup::save_json(
        &output.join("restore-verified.json"),
        &json!({
        "schema":1,"full_original_internal_region":true,"loader_unchanged":true,"remaining_in_dfu":true}),
    )?;
    println!(
        "Original app, added sector and protected neighbors match baseline. Device remains in DFU."
    );
    Ok(())
}

pub async fn start(s: &Stock, b: &Bundle, output: &Path, probe: bool) -> Result<()> {
    new_evidence(output)?;
    let mut session = open(s, true).await?;
    let actual = snapshot(&mut session, s, output, "before-start").await?;
    let expected = if probe { &b.overlay_state } else { &s.baseline };
    ensure!(
        &actual.internal == expected,
        "Installed bytes differ from requested start target; staying in DFU"
    );
    backup::save_json(
        &output.join("before-start-verified.json"),
        &json!({"loader_matches":true,"full_internal_matches":true,"probe":probe}),
    )?;
    let result = session.reader.device.jump_to_application().await;
    drop(session);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
    while tokio::time::Instant::now() < deadline {
        if let Ok(found) = find_firmware_device(&selector(s)).await {
            if found.mode == FirmwareUsbMode::Application
                && found.product_string.as_deref() == Some("POWER-Z KM003C")
            {
                let mut app =
                    KM003C::new(DeviceConfig::vendor().skip_reset().select(selector(s))).await?;
                let info = app.get_device_info().await?;
                ensure!(
                    info.model == "KM003C"
                        && info.hw_version == "2.3"
                        && info.fw_version == "2.0.7"
                        && info.serial_id == s.manifest.identity.serial,
                    "Started application identity differs"
                );
                let mut running = Session {
                    reader: backup::Reader {
                        device: app,
                        dfu: false,
                    },
                    mode: FirmwareUsbMode::Application,
                };
                let after = snapshot(&mut running, s, output, "after-start").await?;
                ensure!(
                    &after.internal == expected,
                    "Internal bytes changed during application startup"
                );
                let callback = if probe {
                    let at = 0x70000 + 20;
                    Some(u32::from_le_bytes(b.overlay.data()[at..at + 4].try_into()?))
                } else {
                    None
                };
                let observed = if let Some(entry) = callback {
                    observe_gui_callback(&mut running, entry, output).await?
                } else {
                    false
                };
                backup::save_json(
                    &output.join("started.json"),
                    &json!({"schema":1,"probe":probe,"application_reenumerated":true,
                    "firmware":"2.0.7","full_internal_after_start_matches":true,
                    "gui_saved_callback_match":callback.map(|_|observed),"calibration_matches":true,
                    "gui_display_validated":false,"pd_coexistence_validated":false}),
                )?;
                ensure!(
                    !probe || observed,
                    "Application started, but exact GUI callback was not observed; do not reflash blindly"
                );
                if probe {
                    println!(
                        "Probe identity, internal bytes and calibration matched; GUI callback observed: {observed}."
                    );
                } else {
                    println!(
                        "Original stock application, full internal bytes and calibration verified after startup."
                    );
                }
                return Ok(());
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    anyhow::bail!("Application did not re-enumerate; no retry issued. Jump result: {result:?}")
}

async fn observe_gui_callback(session: &mut Session, expected: u32, output: &Path) -> Result<bool> {
    // Exact-build TCB from stock task creation. PendSV saves R4-R11 at TCB.sp;
    // its R8 slot is +16. Observe while the GUI is blocked, without halting it.
    for attempt in 0..12 {
        let tcb = session.reader.read(0x1fffbf2c, 0x30).await?;
        ensure!(
            u16::from_le_bytes(tcb[6..8].try_into()?) == 0xdad8 && &tcb[8..16] == b"TASK_GUI",
            "GUI task control block differs from the exact stock profile"
        );
        let word = |at| u32::from_le_bytes(tcb[at..at + 4].try_into().unwrap());
        let (sp, base, size) = (word(0), word(0x24), word(0x28));
        ensure!(
            base == 0x1fff9760
                && size == 0x1000
                && sp >= base
                && sp <= base + size - 36
                && sp % 4 == 0,
            "GUI saved stack pointer is outside its qualified stack"
        );
        let frame = session.reader.read(sp, 36).await?;
        let r8 = u32::from_le_bytes(frame[16..20].try_into()?);
        backup::save(&output.join(format!("gui-{attempt:02}-tcb.bin")), &tcb)?;
        backup::save(&output.join(format!("gui-{attempt:02}-frame.bin")), &frame)?;
        if r8 == expected {
            backup::save_json(
                &output.join("gui-callback-observed.json"),
                &json!({
                "schema":1,"expected_callback":expected,"observed_saved_r8":r8,
                "task_stack_bounds_match":true,"attempt":attempt,"read_only":true}),
            )?;
            return Ok(true);
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn replacing_a_probe_requires_its_explicit_exact_audited_bytes() {
        assert!(flash_source_allowed(b"stock", b"stock", None));
        assert!(!flash_source_allowed(b"probe", b"stock", None));
        assert!(flash_source_allowed(b"probe", b"stock", Some(b"probe")));
        assert!(!flash_source_allowed(b"probE", b"stock", Some(b"probe")));
        assert!(!flash_source_allowed(b"prob", b"stock", Some(b"probe")));
        assert!(!flash_source_allowed(b"other", b"stock", Some(b"probe")));
    }
    #[test]
    fn recovery_orders_erase_before_original_and_resumes_only_verified_states() {
        assert_eq!(restore_plan(b"stock", b"stock", b"erased"), vec![]);
        assert_eq!(
            restore_plan(b"erased", b"stock", b"erased"),
            vec![RestoreStep::Original]
        );
        assert_eq!(
            restore_plan(b"probe", b"stock", b"erased"),
            vec![RestoreStep::ErasePass, RestoreStep::Original]
        );
        assert_eq!(
            restore_plan(b"interrupted", b"stock", b"erased"),
            vec![RestoreStep::ErasePass, RestoreStep::Original]
        );
    }
}
