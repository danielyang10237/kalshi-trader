{
  "eval_metric": "sharpe_ratio",
  "dataset_path": "data/train.csv",
  "seed": 42,
  "variants": [
    {
      "experiment_id": "baseline_mlp",
      "architecture": "mlp",
      "features": ["price", "volume", "ewma_30"],
      "hyperparams": { "layers": 2, "hidden_dim": 64, "lr": 1e-3 }
    },
    {
      "experiment_id": "lstm_full_features",
      "architecture": "lstm",
      "features": ["price", "volume", "ewma_30", "vol_regime", "order_imbalance"],
      "hyperparams": { "layers": 2, "hidden_dim": 128, "lr": 3e-4 }
    },
    {
      "experiment_id": "transformer_full",
      "architecture": "transformer",
      "features": ["price", "volume", "ewma_30", "vol_regime", "order_imbalance"],
      "hyperparams": { "layers": 4, "heads": 4, "dropout": 0.1, "lr": 3e-4 }
    },
    {
      "experiment_id": "xgboost_ablation",
      "architecture": "xgboost",
      "features": ["price", "volume", "ewma_30"],
      "hyperparams": { "max_depth": 6, "n_estimators": 300, "lr": 0.05 }
    }
  ]
}