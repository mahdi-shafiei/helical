from helical.models.uce.uce import UCE

import json
from accelerate import Accelerator
import anndata as ad

with open('./uce_config.json') as f:
    config = json.load(f)

accelerator = Accelerator(project_dir=config["data_config"]["dir"])
uce = UCE(config["model_config"],
          config["data_config"],
          config["files_config"],
          accelerator=accelerator)

ann_data = ad.read_h5ad(config["data_config"]["adata_path"])
data_loader = uce.process_data(ann_data[:100])
embeddings = uce.get_embeddings(data_loader)

print(embeddings.shape)