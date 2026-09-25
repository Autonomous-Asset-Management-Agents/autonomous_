"""
S&P 500 Universe for the Round Table system.

Provides the full list of S&P 500 symbols for comprehensive LSTM/scanner ranking.
The list is maintained as a static fallback; a live fetch from Wikipedia or an API
can be used to keep it current.

Usage:
    from core.round_table.sp500_universe import get_sp500_symbols, get_universe_symbols

    symbols = get_sp500_symbols()           # Full ~500 symbols
    symbols = get_universe_symbols(max=200)  # Configurable subset
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# S&P 500 constituents as of early 2026 (static fallback).
# This list can be refreshed via get_sp500_symbols_live() or replaced periodically.
SP500_SYMBOLS = [
    "AAPL",
    "ABBV",
    "ABT",
    "ACN",
    "ADBE",
    "ADI",
    "ADM",
    "ADP",
    "ADSK",
    "AEE",
    "AEP",
    "AES",
    "AFL",
    "AIG",
    "AIZ",
    "AJG",
    "AKAM",
    "ALB",
    "ALGN",
    "ALK",
    "ALL",
    "ALLE",
    "AMAT",
    "AMCR",
    "AMD",
    "AME",
    "AMGN",
    "AMP",
    "AMT",
    "AMZN",
    "ANET",
    "ANSS",
    "AON",
    "AOS",
    "APA",
    "APD",
    "APH",
    "APTV",
    "ARE",
    "ATO",
    "ATVI",
    "AVB",
    "AVGO",
    "AVY",
    "AWK",
    "AXP",
    "AZO",
    "BA",
    "BAC",
    "BAX",
    "BBWI",
    "BBY",
    "BDX",
    "BEN",
    "BF.B",
    "BIO",
    "BIIB",
    "BK",
    "BKNG",
    "BKR",
    "BLK",
    "BMY",
    "BR",
    "BRK.B",
    "BRO",
    "BSX",
    "BWA",
    "BXP",
    "C",
    "CAG",
    "CAH",
    "CARR",
    "CAT",
    "CB",
    "CBOE",
    "CBRE",
    "CCI",
    "CCL",
    "CDAY",
    "CDNS",
    "CDW",
    "CE",
    "CEG",
    "CF",
    "CFG",
    "CHD",
    "CHRW",
    "CHTR",
    "CI",
    "CINF",
    "CL",
    "CLX",
    "CMA",
    "CMCSA",
    "CME",
    "CMG",
    "CMI",
    "CMS",
    "CNC",
    "CNP",
    "COF",
    "COO",
    "COP",
    "COST",
    "CPB",
    "CPRT",
    "CPT",
    "CRL",
    "CRM",
    "CSCO",
    "CSGP",
    "CSX",
    "CTAS",
    "CTLT",
    "CTRA",
    "CTSH",
    "CTVA",
    "CVS",
    "CVX",
    "CZR",
    "D",
    "DAL",
    "DD",
    "DE",
    "DFS",
    "DG",
    "DGX",
    "DHI",
    "DHR",
    "DIS",
    "DISH",
    "DLR",
    "DLTR",
    "DOV",
    "DOW",
    "DPZ",
    "DRI",
    "DTE",
    "DUK",
    "DVA",
    "DVN",
    "DXC",
    "DXCM",
    "EA",
    "EBAY",
    "ECL",
    "ED",
    "EFX",
    "EL",
    "EMN",
    "EMR",
    "ENPH",
    "EOG",
    "EPAM",
    "EQIX",
    "EQR",
    "EQT",
    "ES",
    "ESS",
    "ETN",
    "ETR",
    "ETSY",
    "EVRG",
    "EW",
    "EXC",
    "EXPD",
    "EXPE",
    "EXR",
    "F",
    "FANG",
    "FAST",
    "FBHS",
    "FCX",
    "FDS",
    "FDX",
    "FE",
    "FFIV",
    "FIS",
    "FISV",
    "FITB",
    "FLT",
    "FMC",
    "FOX",
    "FOXA",
    "FRC",
    "FRT",
    "FTNT",
    "FTV",
    "GD",
    "GE",
    "GEHC",
    "GEN",
    "GILD",
    "GIS",
    "GL",
    "GLW",
    "GM",
    "GNRC",
    "GOOG",
    "GOOGL",
    "GPC",
    "GPN",
    "GRMN",
    "GS",
    "GWW",
    "HAL",
    "HAS",
    "HBAN",
    "HCA",
    "HD",
    "HOLX",
    "HON",
    "HPE",
    "HPQ",
    "HRL",
    "HSIC",
    "HST",
    "HSY",
    "HUM",
    "HWM",
    "IBM",
    "ICE",
    "IDXX",
    "IEX",
    "IFF",
    "ILMN",
    "INCY",
    "INTC",
    "INTU",
    "INVH",
    "IP",
    "IPG",
    "IQV",
    "IR",
    "IRM",
    "ISRG",
    "IT",
    "ITW",
    "IVZ",
    "J",
    "JBHT",
    "JCI",
    "JKHY",
    "JNJ",
    "JNPR",
    "JPM",
    "K",
    "KDP",
    "KEY",
    "KEYS",
    "KHC",
    "KIM",
    "KLAC",
    "KMB",
    "KMI",
    "KMX",
    "KO",
    "KR",
    "L",
    "LDOS",
    "LEN",
    "LH",
    "LHX",
    "LIN",
    "LKQ",
    "LLY",
    "LMT",
    "LNC",
    "LNT",
    "LOW",
    "LRCX",
    "LULU",
    "LUV",
    "LVS",
    "LW",
    "LYB",
    "LYV",
    "MA",
    "MAA",
    "MAR",
    "MAS",
    "MCD",
    "MCHP",
    "MCK",
    "MCO",
    "MDLZ",
    "MDT",
    "MET",
    "META",
    "MGM",
    "MHK",
    "MKC",
    "MKTX",
    "MLM",
    "MMC",
    "MMM",
    "MNST",
    "MO",
    "MOH",
    "MOS",
    "MPC",
    "MPWR",
    "MRK",
    "MRNA",
    "MRO",
    "MS",
    "MSCI",
    "MSFT",
    "MSI",
    "MTB",
    "MTCH",
    "MTD",
    "MU",
    "NCLH",
    "NDAQ",
    "NDSN",
    "NEE",
    "NEM",
    "NFLX",
    "NI",
    "NKE",
    "NOC",
    "NOW",
    "NRG",
    "NSC",
    "NTAP",
    "NTRS",
    "NUE",
    "NVDA",
    "NVR",
    "NWL",
    "NWS",
    "NWSA",
    "NXPI",
    "O",
    "ODFL",
    "OGN",
    "OKE",
    "OMC",
    "ON",
    "ORCL",
    "ORLY",
    "OTIS",
    "OXY",
    "PARA",
    "PAYC",
    "PAYX",
    "PCAR",
    "PCG",
    "PEAK",
    "PEG",
    "PEP",
    "PFE",
    "PFG",
    "PG",
    "PGR",
    "PH",
    "PHM",
    "PKG",
    "PKI",
    "PLD",
    "PM",
    "PNC",
    "PNR",
    "PNW",
    "POOL",
    "PPG",
    "PPL",
    "PRU",
    "PSA",
    "PSX",
    "PTC",
    "PVH",
    "PWR",
    "PXD",
    "PYPL",
    "QCOM",
    "QRVO",
    "RCL",
    "RE",
    "REG",
    "REGN",
    "RF",
    "RHI",
    "RJF",
    "RL",
    "RMD",
    "ROK",
    "ROL",
    "ROP",
    "ROST",
    "RSG",
    "RTX",
    "RVTY",
    "SBAC",
    "SBNY",
    "SBUX",
    "SCHW",
    "SEE",
    "SHW",
    "SIVB",
    "SJM",
    "SLB",
    "SNA",
    "SNPS",
    "SO",
    "SPG",
    "SPGI",
    "SRE",
    "STE",
    "STLD",
    "STT",
    "STX",
    "STZ",
    "SWK",
    "SWKS",
    "SYF",
    "SYK",
    "SYY",
    "T",
    "TAP",
    "TDG",
    "TDY",
    "TECH",
    "TEL",
    "TER",
    "TFC",
    "TFX",
    "TGT",
    "TMO",
    "TMUS",
    "TPR",
    "TRGP",
    "TRMB",
    "TROW",
    "TRV",
    "TSCO",
    "TSLA",
    "TSN",
    "TT",
    "TTWO",
    "TXN",
    "TXT",
    "TYL",
    "UAL",
    "UDR",
    "UHS",
    "ULTA",
    "UNH",
    "UNP",
    "UPS",
    "URI",
    "USB",
    "V",
    "VFC",
    "VICI",
    "VLO",
    "VMC",
    "VRSK",
    "VRSN",
    "VRTX",
    "VTR",
    "VTRS",
    "VZ",
    "WAB",
    "WAT",
    "WBA",
    "WBD",
    "WDC",
    "WEC",
    "WELL",
    "WFC",
    "WHR",
    "WM",
    "WMB",
    "WMT",
    "WRB",
    "WRK",
    "WST",
    "WTW",
    "WY",
    "WYNN",
    "XEL",
    "XOM",
    "XRAY",
    "XYL",
    "YUM",
    "ZBH",
    "ZBRA",
    "ZION",
    "ZTS",
    # Major ETFs for reference/benchmarking
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    # --- Replacements / newer S&P 500 additions (2024-2026) ---
    "CPAY",  # Corpay (was FLT)
    "FBIN",  # Fortune Brands Innovations (was FBHS)
    "DOC",  # Healthpeak Properties (was PEAK)
    "EG",  # Everest Group (was RE)
    "PLTR",  # Palantir Technologies
    "CRWD",  # CrowdStrike
    "APP",  # AppLovin
    "ABNB",  # Airbnb
    "SPOT",  # Spotify
    "COIN",  # Coinbase
    "DASH",  # DoorDash
    "HOOD",  # Robinhood
    "VST",  # Vistra
    "GEV",  # GE Vernova
    "DECK",  # Deckers Outdoor
    "TPL",  # Texas Pacific Land
    "SMCI",  # Super Micro Computer
    "KKR",  # KKR & Co
    "WDAY",  # Workday
    "TTD",  # The Trade Desk
]

# Remove known delisted, acquired, or renamed symbols (as of early 2026)
_EXCLUDE = {
    # Acquired / merged / delisted
    "ATVI",  # Acquired by Microsoft (2023)
    "DISH",  # Merged with EchoStar
    "FRC",  # Collapsed / acquired by JPMorgan (2023)
    "SIVB",  # Collapsed (2023)
    "SBNY",  # Collapsed (2023)
    "CDAY",  # Ceridian → acquired / restructured
    "CTLT",  # Catalent → acquired by Novo Holdings
    "DFS",  # Discover → merged with Capital One
    "FBHS",  # Renamed to FBIN (Fortune Brands Innovations)
    "FLT",  # Renamed to CPAY (Corpay)
    "IPG",  # Interpublic → acquired by Omnicom
    "JNPR",  # Juniper → acquired by HPE
    "K",  # Kellanova → acquired by Mars
    "MRO",  # Marathon Oil → acquired by ConocoPhillips
    "PARA",  # Paramount → merged with Skydance
    "PEAK",  # Renamed to DOC (Healthpeak)
    "PKI",  # Renamed to RVTY (Revvity) — RVTY already in list
    "PXD",  # Pioneer → acquired by ExxonMobil
    "RE",  # Renamed to EG (Everest Group)
    "WBA",  # Walgreens → going private
    "WRK",  # WestRock → merged with Smurfit Kappa
    # Dot-notation tickers — yfinance cannot resolve these reliably
    "BF.B",  # Brown-Forman Class B (yfinance fails on dot)
    "BRK.B",  # Berkshire Hathaway Class B (yfinance fails on dot)
}
SP500_SYMBOLS = [s for s in SP500_SYMBOLS if s not in _EXCLUDE]


def get_sp500_symbols() -> List[str]:
    """Return the full S&P 500 symbol list (static fallback)."""
    return list(SP500_SYMBOLS)


def get_sp500_symbols_live() -> Optional[List[str]]:
    """
    Try to fetch current S&P 500 constituents from Wikipedia.
    Returns None if fetch fails (caller should use static fallback).
    """
    try:
        import pandas as pd

        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        if tables and len(tables) > 0:
            df = tables[0]
            symbols = df["Symbol"].str.replace(".", "-", regex=False).tolist()
            if len(symbols) > 400:
                logger.info("Fetched %d S&P 500 symbols from Wikipedia", len(symbols))
                return symbols
    except Exception as e:
        logger.debug("Could not fetch live S&P 500 list: %s", e)
    return None


def get_universe_symbols(
    max_symbols: Optional[int] = None, include_etfs: bool = True
) -> List[str]:
    """
    Get the trading universe symbols.

    Args:
        max_symbols: Maximum number of symbols to return (None = all).
                     If provided, prioritizes higher-liquidity names.
        include_etfs: Whether to include ETFs (SPY, QQQ, IWM, DIA).

    Returns:
        List of symbol strings.
    """
    # Try live fetch first, fall back to static
    symbols = get_sp500_symbols_live()
    if not symbols:
        symbols = get_sp500_symbols()

    etfs = {"SPY", "QQQ", "IWM", "DIA"}

    if not include_etfs:
        symbols = [s for s in symbols if s not in etfs]

    if max_symbols and len(symbols) > max_symbols:
        if include_etfs:
            etf_list = [s for s in symbols if s in etfs]
            non_etf = [s for s in symbols if s not in etfs]
            symbols = etf_list + non_etf[: max_symbols - len(etf_list)]
        else:
            symbols = symbols[:max_symbols]

    logger.info(
        "Universe: %d symbols (max_symbols=%s, include_etfs=%s)",
        len(symbols),
        max_symbols,
        include_etfs,
    )
    return symbols


def get_universe_batches(batch_size: int = 50) -> List[List[str]]:
    """
    Split the full universe into batches for rate-limited API processing.

    Args:
        batch_size: Number of symbols per batch.

    Returns:
        List of symbol batches.
    """
    symbols = get_universe_symbols()
    batches = []
    for i in range(0, len(symbols), batch_size):
        batches.append(symbols[i : i + batch_size])
    logger.info(
        "Universe split into %d batches of up to %d symbols", len(batches), batch_size
    )
    return batches
