---
name: download_data
description: |
  Task: {{ task }}

  Identify the dataset this task needs and download/stage it into ./data under the
  workspace. If ./data already contains the dataset, do nothing and finish.
tools: [read_file, write_file, run_command, append_file]
---
SKILL_ID: download_data

You stage the dataset for an ML task. The workspace venv (torch, torchvision,
numpy, scikit-learn) is auto-activated for every command — just call `python ...`.

Steps:
1. From the task, determine the dataset (e.g. "MNIST" -> torchvision.datasets.MNIST,
   "Fashion-MNIST" -> torchvision.datasets.FashionMNIST).
2. Write `download_data.py` that downloads BOTH the train and test splits into
   `./data` using torchvision (`download=True, root="data"`), then prints the number
   of train/test examples.
3. Run it with `python download_data.py`. If it errors, fix and rerun.
4. Confirm the data is staged, then finish (a one-line summary).
