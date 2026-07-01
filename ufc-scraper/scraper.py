#!/usr/bin/env python3

import argparse
import difflib
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import pandas as pd
from bs4 import BeautifulSoup

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages


BASE_URL = "https://www.ufc.com/athletes/all"
UFC_HOME = "https://www.ufc.com"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def make_session():
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def clean_text(tag):
    return tag.get_text(" ", strip=True) if tag else ""


def normalize_key(text):
    """
    Example:
    'Sig. Str. Landed' -> 'sig_str_landed'
    'Win By Method' -> 'win_by_method'
    """
    text = str(text).lower().strip()
    text = text.replace(".", "")
    text = text.replace("%", "percent")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def fighter_name_to_slug(fighter_name):
    """
    Example:
    'Conor McGregor' -> 'conor-mcgregor'
    """
    slug = fighter_name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def parse_record(record_text):
    """
    Example:
    '22-6-0 (W-L-D)' -> wins=22, losses=6, draws=0, total=28
    """
    match = re.search(r"(\d+)\s*-\s*(\d+)\s*-\s*(\d+)", record_text or "")
    if not match:
        return 0, 0, 0, 0

    wins, losses, draws = map(int, match.groups())
    total_fights = wins + losses + draws
    return wins, losses, draws, total_fights


def find_profile_url_from_card(card, fullname=""):
    """
    Tries to find the fighter profile URL from the listing card.
    If no link is found, it creates the normal UFC slug URL.
    """
    profile_url = ""

    link = card.find_parent("a", href=re.compile(r"/athlete/"))
    if link:
        profile_url = urljoin(UFC_HOME, link["href"])

    if not profile_url:
        for parent in card.parents:
            possible_link = parent.find("a", href=re.compile(r"/athlete/")) if parent else None
            if possible_link:
                profile_url = urljoin(UFC_HOME, possible_link["href"])
                break

    if not profile_url and fullname:
        profile_url = f"{UFC_HOME}/athlete/{fighter_name_to_slug(fullname)}"

    return profile_url


def scrape_ufc_athletes(start_page=0, sleep_seconds=0.5, max_safety_pages=500):
    """
    Scrapes UFC athletes.

    It starts at page=0, then page=1, page=2, etc.
    It stops when a page has no athlete cards.
    max_safety_pages prevents an infinite loop if the website behaves strangely.
    """
    scraped_data = []
    session = make_session()
    page_number = start_page

    while page_number < max_safety_pages:
        url = f"{BASE_URL}?gender=All&search=&page={page_number}"
        response = session.get(url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        athlete_cards = soup.find_all("div", class_="c-listing-athlete__text")

        if len(athlete_cards) == 0:
            print(f"No athletes found on page {page_number}. Finished scraping.")
            break

        print(f"Scraping page {page_number} — found {len(athlete_cards)} athletes")

        for card in athlete_cards:
            nickname = clean_text(card.find("span", class_="c-listing-athlete__nickname"))
            fullname = clean_text(card.find("span", class_="c-listing-athlete__name"))
            weight = clean_text(card.find("span", class_="c-listing-athlete__title"))
            record = clean_text(card.find("span", class_="c-listing-athlete__record"))

            totalwins, totalloss, totaldraws, totalfights = parse_record(record)
            profile_url = find_profile_url_from_card(card, fullname)

            scraped_data.append(
                {
                    "Nickname": nickname,
                    "Fullname": fullname,
                    "Weight": weight,
                    "Record": record,
                    "ProfileURL": profile_url,
                    "totalwins": totalwins,
                    "totalloss": totalloss,
                    "totaldraws": totaldraws,
                    "totalfights": totalfights,
                }
            )

        page_number += 1
        time.sleep(sleep_seconds)

    return scraped_data


def find_fighter_candidates(df, fighter_name, limit=10):
    """
    Finds possible fighter matches from the scraped athlete DataFrame.
    This helps confirm spelling before scraping a profile page.
    """
    query = fighter_name.lower().strip()

    df_copy = df.copy()
    df_copy["name_lower"] = df_copy["Fullname"].fillna("").str.lower()

    contains_matches = df_copy[df_copy["name_lower"].str.contains(query, na=False, regex=False)]

    if not contains_matches.empty:
        return contains_matches[
            ["Fullname", "Nickname", "Weight", "Record", "ProfileURL"]
        ].head(limit)

    names = df_copy["Fullname"].dropna().tolist()

    close_names = difflib.get_close_matches(
        fighter_name,
        names,
        n=limit,
        cutoff=0.4,
    )

    fuzzy_matches = df_copy[df_copy["Fullname"].isin(close_names)]

    return fuzzy_matches[
        ["Fullname", "Nickname", "Weight", "Record", "ProfileURL"]
    ].head(limit)


def get_fighter_profile_url(df, fighter_name):
    """
    Uses the scraped athlete list to find the fighter's profile URL.
    If it cannot find an exact match, it creates a UFC-style slug URL.
    """
    query = fighter_name.lower().strip()
    matches = df[df["Fullname"].fillna("").str.lower() == query]

    if not matches.empty:
        profile_url = matches.iloc[0].get("ProfileURL", "")
        if isinstance(profile_url, str) and profile_url.strip():
            return profile_url

    return f"{UFC_HOME}/athlete/{fighter_name_to_slug(fighter_name)}"


def extract_top_stat(page_text, label):
    """
    Extracts stats like:
    19 Wins By Knockout
    1 Wins By Submission
    """
    pattern = rf"(\d+)\s+{re.escape(label)}"
    match = re.search(pattern, page_text, re.IGNORECASE)

    if match:
        return match.group(1)

    return ""


def extract_accuracy_stats(page_text, stats):
    """
    Extracts:
    Striking Accuracy
    Takedown Accuracy
    """
    striking_pattern = (
        r"Striking\s+Accuracy\s+"
        r"(\d+%)\s+"
        r"Sig\.?\s+Strikes\s+Landed\s+(\d+)\s+"
        r"Sig\.?\s+Strikes\s+Attempted\s+(\d+)"
    )

    striking_match = re.search(striking_pattern, page_text, re.IGNORECASE)

    if striking_match:
        stats["striking_accuracy"] = striking_match.group(1)
        stats["sig_strikes_landed_total"] = striking_match.group(2)
        stats["sig_strikes_attempted_total"] = striking_match.group(3)

    takedown_pattern = (
        r"Takedown\s+Accuracy\s+"
        r"(\d+%)\s+"
        r"Takedowns\s+Landed\s+(\d+)\s+"
        r"Takedowns\s+Attempted\s+(\d+)"
    )

    takedown_match = re.search(takedown_pattern, page_text, re.IGNORECASE)

    if takedown_match:
        stats["takedown_accuracy"] = takedown_match.group(1)
        stats["takedowns_landed_total"] = takedown_match.group(2)
        stats["takedowns_attempted_total"] = takedown_match.group(3)

    return stats


def extract_compare_stats(soup, stats):
    """
    Extracts stats from blocks like:
    Sig. Str. Landed
    Sig. Str. Absorbed
    Takedown Avg
    Submission Avg
    Sig. Str. Defense
    Takedown Defense
    Knockdown Avg
    Average Fight Time
    """
    compare_blocks = soup.select(".c-stat-compare")

    for block in compare_blocks:
        groups = block.select(".c-stat-compare__group")

        for group in groups:
            number = clean_text(group.select_one(".c-stat-compare__number"))
            percent = clean_text(group.select_one(".c-stat-compare__percent"))
            label = clean_text(group.select_one(".c-stat-compare__label"))
            suffix = clean_text(group.select_one(".c-stat-compare__label-suffix"))

            if not label:
                continue

            key = normalize_key(label)
            value = number

            if percent and percent not in value:
                value = f"{value}{percent}"

            stats[key] = value

            if suffix:
                stats[f"{key}_suffix"] = suffix

    return stats


def extract_three_bar_stats(soup, stats):
    """
    Extracts:
    Sig. Str. By Position: Standing, Clinch, Ground
    Win By Method: KO/TKO, DEC, SUB
    """
    three_bar_blocks = soup.select(".c-stat-3bar")

    for block in three_bar_blocks:
        title = clean_text(block.select_one(".c-stat-3bar__title"))
        title_key = normalize_key(title)
        groups = block.select(".c-stat-3bar__group")

        for group in groups:
            label = clean_text(group.select_one(".c-stat-3bar__label"))
            value = clean_text(group.select_one(".c-stat-3bar__value"))

            if label and value:
                key = f"{title_key}_{normalize_key(label)}"
                stats[key] = value

    return stats


def extract_body_target_stats(soup, stats):
    """
    Extracts:
    Sig. Str. By Target: Head, Body, Leg

    UFC stores some of these inside SVG <text> elements.
    """
    body_blocks = soup.select(".c-stat-body")

    for block in body_blocks:
        title = clean_text(block.select_one(".c-stat-body__title"))
        title_key = normalize_key(title)

        for part in ["head", "body", "leg"]:
            count_value = ""
            percent_value = ""

            text_tags = block.find_all("text", id=True)

            for text_tag in text_tags:
                text_id = text_tag.get("id", "").lower()
                text_id = text_id.replace("x5f", "_")
                text_value = text_tag.get_text(strip=True)

                if re.search(rf"[_-]{part}[_-]value$", text_id):
                    count_value = text_value

                if re.search(rf"[_-]{part}[_-]percent$", text_id):
                    percent_value = text_value

            if count_value:
                stats[f"{title_key}_{part}_count"] = count_value

            if percent_value:
                stats[f"{title_key}_{part}_percent"] = percent_value

    return stats


def scrape_athlete_stats(fighter_name, df=None, profile_url=None):
    """
    Scrapes one UFC athlete profile page.

    Example:
    stats = scrape_athlete_stats("Conor McGregor", df=df)
    """
    session = make_session()

    if profile_url is None:
        if df is not None:
            profile_url = get_fighter_profile_url(df, fighter_name)
        else:
            profile_url = f"{UFC_HOME}/athlete/{fighter_name_to_slug(fighter_name)}"

    response = session.get(profile_url, timeout=30)

    if response.status_code != 200:
        raise ValueError(
            f"Could not load profile page. "
            f"Status code: {response.status_code}. "
            f"URL: {profile_url}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    if "Page not found" in page_text or "404" in page_text[:500]:
        raise ValueError(f"Fighter page does not appear to exist: {profile_url}")

    stats = {
        "fighter_name": fighter_name,
        "profile_url": profile_url,
    }

    stats["wins_by_knockout"] = extract_top_stat(page_text, "Wins By Knockout")
    stats["wins_by_submission"] = extract_top_stat(page_text, "Wins By Submission")

    stats = extract_accuracy_stats(page_text, stats)
    stats = extract_compare_stats(soup, stats)
    stats = extract_three_bar_stats(soup, stats)
    stats = extract_body_target_stats(soup, stats)

    return stats


def generate_pdf(scraped_data, pdf_path, max_charts=None):
    """
    Creates a multi-page PDF.
    Each page contains one athlete's win/loss/draw pie chart.
    """
    if not scraped_data:
        raise ValueError("scraped_data is empty. Run the scraper first.")

    charts_created = 0

    with PdfPages(pdf_path) as pdf:
        for row in scraped_data:
            total_wins = int(row["totalwins"])
            total_losses = int(row["totalloss"])
            total_draws = int(row["totaldraws"])

            total = total_wins + total_losses + total_draws

            if total == 0:
                continue

            fig, ax = plt.subplots(figsize=(5, 5))

            values = [total_wins, total_losses, total_draws]
            labels = ["Total Wins", "Total Losses", "Total Draws"]

            ax.pie(
                values,
                labels=labels,
                autopct="%1.1f%%",
                shadow=True,
                startangle=90,
                explode=(0.1, 0, 0),
            )

            ax.axis("equal")

            athlete_name = row["Fullname"] or "Unknown Athlete"
            ax.set_title(athlete_name)

            ax.text(
                0,
                -1.25,
                f"Wins: {total_wins} | Losses: {total_losses} | Draws: {total_draws}",
                ha="center",
            )

            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

            charts_created += 1

            if max_charts is not None and charts_created >= max_charts:
                break

    print(f"Created {charts_created} charts.")
    print(f"PDF saved to: {pdf_path}")

    return pdf_path


def save_selected_fighter_stats(df, fighter_name, output_dir):
    """
    Finds and scrapes stats for one selected fighter.
    Saves the result to selected_fighter_stats.csv.
    """
    matches = find_fighter_candidates(df, fighter_name)

    if matches.empty:
        print(f"No close matches found for: {fighter_name}")
        selected_fighter_name = fighter_name
        selected_profile_url = f"{UFC_HOME}/athlete/{fighter_name_to_slug(fighter_name)}"
    else:
        print("Possible fighter matches:")
        print(matches.reset_index(drop=True).to_string(index=True))

        exact_matches = matches[
            matches["Fullname"].fillna("").str.lower() == fighter_name.lower().strip()
        ]

        if not exact_matches.empty:
            selected_row = exact_matches.iloc[0]
        else:
            selected_row = matches.iloc[0]

        selected_fighter_name = selected_row["Fullname"]
        selected_profile_url = selected_row["ProfileURL"]

    print(f"Scraping stats for: {selected_fighter_name}")
    print(f"Profile URL: {selected_profile_url}")

    stats = scrape_athlete_stats(
        fighter_name=selected_fighter_name,
        df=df,
        profile_url=selected_profile_url,
    )

    stats_df = pd.DataFrame([stats]).T.reset_index()
    stats_df.columns = ["Stat", "Value"]

    print("Selected fighter stats:")
    print(stats_df.to_string(index=False))

    stats_csv_path = output_dir / "selected_fighter_stats.csv"
    stats_df.to_csv(stats_csv_path, index=False)

    print(f"Saved selected fighter stats to: {stats_csv_path}")

    return stats_df


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape UFC athlete records and generate CSV/PDF outputs."
    )

    parser.add_argument(
        "--fighter",
        default="Conor McGregor",
        help="Fighter name to scrape individual profile stats for.",
    )

    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory where CSV and PDF files will be saved.",
    )

    parser.add_argument(
        "--start-page",
        type=int,
        default=0,
        help="UFC athletes listing page to start scraping from.",
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between UFC listing page requests.",
    )

    parser.add_argument(
        "--max-safety-pages",
        type=int,
        default=500,
        help="Maximum number of listing pages to scrape before stopping.",
    )

    parser.add_argument(
        "--max-charts",
        type=int,
        default=None,
        help="Optional limit for number of PDF charts. Useful for quick tests.",
    )

    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="Skip generating the PDF file.",
    )

    parser.add_argument(
        "--skip-fighter-stats",
        action="store_true",
        help="Skip scraping individual fighter profile stats.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    athletes_csv_path = output_dir / "ufc_athletes.csv"
    pdf_path = output_dir / "ufc_athlete_records.pdf"

    print("Starting UFC athlete scrape...")
    print(f"Output directory: {output_dir}")

    scraped_data = scrape_ufc_athletes(
        start_page=args.start_page,
        sleep_seconds=args.sleep,
        max_safety_pages=args.max_safety_pages,
    )

    df = pd.DataFrame(scraped_data)

    print(f"Scraped {len(df)} athletes total.")

    if not df.empty:
        print("First 20 scraped athletes:")
        print(df.head(20).to_string(index=False))

    df.to_csv(athletes_csv_path, index=False)
    print(f"Saved athlete list to: {athletes_csv_path}")

    if not args.skip_fighter_stats:
        save_selected_fighter_stats(
            df=df,
            fighter_name=args.fighter,
            output_dir=output_dir,
        )

    if not args.skip_pdf:
        generate_pdf(
            scraped_data=scraped_data,
            pdf_path=pdf_path,
            max_charts=args.max_charts,
        )

    print("Done.")


if __name__ == "__main__":
    main()