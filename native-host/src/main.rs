// SPDX-License-Identifier: MIT
mod bundle;
mod device;
use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(about = "Exact HW2.3 stock-2.0.7 overlay packaging and qualified native-loader access")]
struct Args {
    #[arg(long)]
    stock: PathBuf,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Read-only private SRAM snapshot for exact-build GUI analysis.
    InspectGui {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Verify a known healthy application before requesting native DFU. No flash write.
    EnterDfu {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Offline only: create extended overlay, erase pass and original restore bundle.
    Package {
        #[arg(long)]
        candidate: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Offline only: verify package bytes, profile and complete modeled rollback.
    Audit {
        #[arg(long)]
        bundle: PathBuf,
    },
    /// Read only; accepts stock APP or manually entered native DFU.
    CheckDevice {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// In manually entered native DFU: install the call-through probe; never starts it.
    FlashProbe {
        #[arg(long)]
        bundle: PathBuf,
        /// Audited previous probe whose complete installed bytes may be replaced.
        #[arg(long)]
        previous_bundle: Option<PathBuf>,
        #[arg(long)]
        output: PathBuf,
    },
    /// In native DFU: erase added sectors, then restore exact original application.
    Restore {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Verify all installed bytes in native DFU before starting the probe.
    StartProbe {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Verify exact original bytes in native DFU before starting stock.
    StartStock {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
}
#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    if let Command::Package { candidate, output } = args.command {
        return bundle::package(&args.stock, &candidate, &output);
    }
    let s = bundle::stock(&args.stock)?;
    match args.command {
        Command::InspectGui {
            bundle: path,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            device::inspect_gui(&s, &b, &output).await?;
        }
        Command::EnterDfu {
            bundle: path,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            device::enter_dfu(&s, &b, &output).await?;
        }
        Command::Audit { bundle: path } => {
            let _ = bundle::audit(&s, &args.stock, &path)?;
            println!("Offline bundle audit passed. No USB accessed.");
        }
        Command::CheckDevice {
            bundle: path,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            device::check(&s, &b, &output).await?;
        }
        Command::FlashProbe {
            bundle: path,
            previous_bundle,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            let previous = previous_bundle
                .as_ref()
                .map(|path| bundle::audit(&s, &args.stock, path))
                .transpose()?;
            device::flash(&s, &b, previous.as_ref(), &output).await?;
        }
        Command::Restore {
            bundle: path,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            device::restore(&s, &b, &output).await?;
        }
        Command::StartProbe {
            bundle: path,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            device::start(&s, &b, &output, true).await?;
        }
        Command::StartStock {
            bundle: path,
            output,
        } => {
            let b = bundle::audit(&s, &args.stock, &path)?;
            device::start(&s, &b, &output, false).await?;
        }
        Command::Package { .. } => unreachable!(),
    }
    Ok(())
}
