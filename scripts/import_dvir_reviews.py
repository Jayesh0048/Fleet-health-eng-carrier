import argparse
from dvir_learning.store import import_reviews, reviewed_counts

parser = argparse.ArgumentParser(description="Import human-reviewed DVIR labels from CSV.")
parser.add_argument("csv_file")
parser.add_argument("--db", default=None)
args = parser.parse_args()
count = import_reviews(args.csv_file, args.db)
print(f"Imported/updated {count} reviewed rows")
print(f"Current reviewed counts: {reviewed_counts(args.db)}")
