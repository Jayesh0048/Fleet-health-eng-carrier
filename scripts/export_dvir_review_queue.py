import argparse
from dvir_learning.store import export_review_queue, reviewed_counts

parser = argparse.ArgumentParser(description="Export unreviewed DVIR parser observations for human labeling.")
parser.add_argument("--out", default="data/dvir_review_queue.csv")
parser.add_argument("--db", default=None)
args = parser.parse_args()
count = export_review_queue(args.out, args.db)
print(f"Exported {count} rows to {args.out}")
print(f"Current reviewed counts: {reviewed_counts(args.db)}")
