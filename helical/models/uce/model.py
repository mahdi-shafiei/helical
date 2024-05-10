import logging
import numpy as np
from anndata import AnnData
from torch.utils.data import DataLoader
import os
from pathlib import Path
from helical.models.uce.uce_config import UCEConfig
from helical.models.helical import HelicalBaseModel
from helical.models.uce.uce_utils import get_ESM2_embeddings, load_model, process_data, get_gene_embeddings
from accelerate import Accelerator
from helical.services.downloader import Downloader
from typing import Optional

class UCE(HelicalBaseModel):
    """Universal Cell Embedding Model. This model reads in single-cell RNA-seq data and outputs gene embeddings. 
        This model particularly uses protein-embeddings generated by ESM2. 
        Currently we support human and macaque species but you can add your own species by providing the protein embeddings.

        Example
        -------
        >>> from helical.models import UCE,UCEConfig
        >>> import anndata as ad
        >>> model_config=UCEConfig(batch_size=10)
        >>> uce = UCE(model_config=model_config)
        >>> ann_data = ad.read_h5ad("./data/10k_pbmcs_proc.h5ad")
        >>> dataset = uce.process_data(ann_data[:100])
        >>> embeddings = uce.get_embeddings(dataset)

        Parameters
        ----------
        model_dir : str, optional, default = None
            The path to the model directory. None by default, which will download the model if not present.
        model_config : UCEConfig, optional, default = default_config
            The model configuration.

        Returns
        -------
        None

        Notes
        -----
        The Universal Cell Embedding Papers has been published on `BioRxiv <https://www.biorxiv.org/content/10.1101/2023.11.28.568918v1>`_ and it is built on top of `SATURN <https://www.nature.com/articles/s41592-024-02191-z>`_ published in Nature.
        """
    default_config = UCEConfig()

    def __init__(self, model_dir: Optional[str] = None, model_config: UCEConfig = default_config) -> None:    
        super().__init__()
        self.model_config = model_config.config
        self.log = logging.getLogger("UCE-Model")
        self.downloader = Downloader()
        print("Start UCE",flush=True)
        if model_dir is None:
            
            self.downloader.download_via_name("uce/all_tokens.torch")
            self.downloader.download_via_name("uce/species_chrom.csv")
            self.downloader.download_via_name("uce/species_offsets.pkl")
            self.downloader.download_via_name("uce/protein_embeddings/Homo_sapiens.GRCh38.gene_symbol_to_embedding_ESM2.pt")
            self.downloader.download_via_name("uce/protein_embeddings/Macaca_fascicularis.Macaca_fascicularis_6.0.gene_symbol_to_embedding_ESM2.pt")
            self.model_dir = Path(os.path.join(self.downloader.CACHE_DIR_HELICAL,'uce'))
            
            if self.model_config['n_layers']==33:
                self.downloader.download_via_name("uce/33l_8ep_1024t_1280.torch")
                model_path = self.model_dir / "33l_8ep_1024t_1280.torch"
            elif self.model_config['n_layers']==4:
                self.downloader.download_via_name("uce/4layer_model.torch")
                model_path = self.model_dir / "4layer_model.torch"
            else:
                raise("Currently you have to chose between 'n_layers'= 4 or 33 to load a pre-trained model.")
            
           
        else:
            self.model_dir = Path(model_dir)
            if self.model_config['n_layers']==33:
                model_path = self.model_dir / "33l_8ep_1024t_1280.torch"
            elif self.model_config['n_layers']==4:
                model_path = self.model_dir / "4layer_model.torch"
            else:
                raise("Currently you have to chose between 'n_layers'= 4 or 33 to load a pre-trained model.")
        


        token_file = self.model_dir / "all_tokens.torch"
        print("Start Embeddings",flush=True)
        self.embeddings = get_ESM2_embeddings(token_file, self.model_config["token_dim"])
        print("Embeddings Loaded",flush=True)
        self.model =  load_model(model_path, self.model_config, self.embeddings)
        print("Model Loaded",flush=True)
        self.model = self.model.eval()

        if self.model_config["accelerator"]:
            self.accelerator = Accelerator(project_dir=self.model_dir, cpu=self.model_config["accelerator"]["cpu"])
            self.model = self.accelerator.prepare(self.model)
        else:
            self.accelerator = None
        print("Accelerator done",flush=True)

    def process_data(self, data: AnnData, 
                     species: str = "human", 
                     filter_genes_min_cell: int = None, 
                     embedding_model: str = "ESM2" ) -> DataLoader:
        """Processes the data for the Universal Cell Embedding model

        Parameters 
        ----------
        data : AnnData
            The AnnData object containing the data to be processed. 
            The UCE model requires the gene expression data as input and the gene symbols as variable names (i.e. as adata.var_names).
        species: str, optional, default = "human"
            The species of the data.  Currently we support "human" and "macaca_fascicularis" but more embeddings will come soon.
        filter_genes_min_cell: int, default = None
            Filter threshold that defines how many times a gene should occur in all the cells.
        embedding_model: str, optional, default = "ESM2"
            The name of the gene embedding model. The current option is only ESM2.

        Returns
        -------
        DataLoader
            The DataLoader object containing the processed data
        """
        
        files_config = {
            "spec_chrom_csv_path": self.model_dir / "species_chrom.csv",
            "protein_embeddings_dir": self.model_dir / "protein_embeddings/",
            "offset_pkl_path": self.model_dir / "species_offsets.pkl"
        }

        data_loader = process_data(data, 
                              model_config=self.model_config, 
                              files_config=files_config,
                              species=species,
                              filter_genes_min_cell=filter_genes_min_cell,
                              embedding_model=embedding_model,
                              accelerator=self.accelerator)
        return data_loader

    def get_embeddings(self, dataloader: DataLoader) -> np.array:
        """Gets the gene embeddings from the UCE model

        Parameters
        ----------
        dataloader : DataLoader
            The DataLoader object containing the processed data

        Returns
        -------
        np.array
            The gene embeddings in the form of a numpy array
        """
        self.log.info(f"Inference started")
        embeddings = get_gene_embeddings(self.model, dataloader, self.accelerator)
        return embeddings
