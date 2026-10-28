"""Automatic annotation (pre-validation) report from aws_annotator_platform export zips.

  loader.py     reads the platform's export layout (zip or folder): annotations.json, imgs/, masks/
  metrics.py    statistics: Wilson CI, Cohen's kappa, Dice, image QC, perceptual hash
  analysis.py   builds the report data (one dict, also written as report.json)
  render.py     renders that dict as a self-contained Korean HTML report (print to PDF from the browser)
  ai_predict.py runs the platform's model service (auto segmentation) over an export -> AI predictions
  cli.py        python -m annotation_report report|predict|demo
"""
