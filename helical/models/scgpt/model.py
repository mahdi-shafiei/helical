import os
import scanpy as sc
from helical.models.helical import HelicalRNAModel
from helical.models.scgpt.scgpt_config import scGPTConfig
import numpy as np
from anndata import AnnData
import logging
from typing import Literal
from accelerate import Accelerator
from helical.models.scgpt.scgpt_utils import load_model
from helical.models.scgpt.dataset import Dataset
from helical.services.downloader import Downloader
from helical.models.scgpt.data_collator import DataCollator
from torch.utils.data import DataLoader, SequentialSampler
import torch
from tqdm import tqdm

LOGGER = logging.getLogger(__name__)
os.environ['KMP_DUPLICATE_LIB_OK']='True'

class scGPT(HelicalRNAModel):
    """scGPT Model. 
        The scGPT Model is a transformer-based model that can be used to extract gene embeddings from single-cell RNA-seq data.
        Currently we load the continous pre-training model from the scGPT repository as default model which works best on zero-shot tasks.


        Example
        -------
        >>> from helical.models import scGPT,scGPTConfig
        >>> import anndata as ad
        >>> scgpt_config=scGPTConfig(batch_size=10)
        >>> scgpt = scGPT(configurer=scgpt_config)
        >>> ann_data = ad.read_h5ad("./data/10k_pbmcs_proc.h5ad")
        >>> dataset = scgpt.process_data(ann_data[:100])
        >>> embeddings = scgpt.get_embeddings(dataset)


        Parameters
        ----------
        configurer : scGPTConfig, optional, default = configurer
            The model configuration.

        Returns
        -------
        None

        Notes
        -----
        We use the implementation from this `repository <https://github.com/bowang-lab/scGPT>`_ , which comes from the original authors. You can find the description of the method in this `paper <https://www.nature.com/articles/s41592-024-02201-0>`_.
        """
    configurer = scGPTConfig()

    def __init__(self, configurer: scGPTConfig = configurer) -> None:
          
        super().__init__()
        self.config = configurer.config
        
        downloader = Downloader()
        for file in self.config["list_of_files_to_download"]:
            downloader.download_via_name(file)

        self.model, self.vocab = load_model(self.config)
        
        if self.config["accelerator"]:
            self.accelerator = Accelerator(project_dir=self.config["model_path"].parent)
            self.model = self.accelerator.prepare(self.model)
        else:
            self.accelerator = None
        LOGGER.info(f"Model finished initializing.")
        
    def get_embeddings(self, dataset: Dataset) -> np.array:
        """Gets the gene embeddings

        Parameters 
        ----------
        dataset: Dataset
            The processed dataset to get the embeddings from.

        Returns
        -------
        np.array
            The gene embeddings in the form of a numpy array
        """
        LOGGER.info(f"Inference started:")

        try:
            use_batch_labels = dataset.batch_ids is not None
        except:
            use_batch_labels = False
            
        collator = DataCollator(
            do_padding=True,
            pad_token_id=self.vocab[self.config["pad_token"]],
            pad_value=self.config["pad_value"],
            do_mlm=False,
            do_binning=True,
            max_length=1200,
            sampling=True,
            keep_first_n_tokens=1,
        )
        data_loader = DataLoader(
            dataset,
            batch_size=self.config["batch_size"],
            sampler=SequentialSampler(dataset),
            collate_fn=collator,
            drop_last=False,
            pin_memory=True,
        )

        device = next(self.model.parameters()).device
        cell_embeddings = np.zeros(
            (len(dataset), self.config["embsize"]), dtype=np.float32
        )
        with torch.no_grad(), torch.cuda.amp.autocast(enabled=True): #torch.autocast(device_type=str(device),enabled=True): # torch.cuda.amp.autocast(enabled=True):
            count = 0
            for data_dict in tqdm(data_loader, desc="Embedding cells"):
                input_gene_ids = data_dict["gene"].to(device)
                src_key_padding_mask = input_gene_ids.eq(
                    self.vocab[self.config["pad_token"]]
                )
                embeddings = self.model._encode(
                    input_gene_ids,
                    data_dict["expr"].to(device),
                    src_key_padding_mask=src_key_padding_mask,
                    batch_labels=data_dict["batch_labels"].to(device)
                    if use_batch_labels
                    else None,
                )

                embeddings = embeddings[:, 0, :]  # get the <cls> position embedding
                embeddings = embeddings.cpu().numpy()
                cell_embeddings[count : count + len(embeddings)] = embeddings
                count += len(embeddings)
        cell_embeddings = cell_embeddings / np.linalg.norm(
            cell_embeddings, axis=1, keepdims=True
        )

        # TODO?
        # return_new_adata (bool): Whether to return a new AnnData object. If False, will
        #     add the cell embeddings to a new :attr:`adata.obsm` with key "X_scGPT".
        # if return_new_adata:
        #     obs_df = adata.obs[obs_to_save] if obs_to_save is not None else None
        #     return sc.AnnData(X=cell_embeddings, obs=obs_df, dtype="float32")
        # adata.obsm["X_scGPT"] = cell_embeddings

        return cell_embeddings
    
    def process_data(self,
                     adata: AnnData, 
                     gene_column_name: str = "index", 
                     fine_tuning: bool = False,
                     n_top_genes: int = 1800, 
                     flavor: Literal["seurat", "cell_ranger", "seurat_v3", "seurat_v3_paper"] = "seurat_v3",
                     use_batch_labels: bool = False
    ) -> Dataset:
        """Processes the data for the scGPT model

        Parameters 
        ----------
        data : AnnData
            The AnnData object containing the data to be processed. 
            The Anndata requires the expression counts as the data matrix and the column with the gene symbols is defined by the argument gene_column_name.
        gene_column_name: str, optional, default = "index"
            The column in adata.var that contains the gene names. Default is to use the "index" column.
        fine_tuning: bool, optional, default = False
            If you intend to use the data to fine-tune the model on a downstream task, set this to True.
        n_top_genes: int, optional, default = 1800
           Only taken into account if you use the dataset for fine-tuning the model. Number of highly-variable genes to keep. Mandatory if flavor='seurat_v3'.
        flavor: Literal["seurat", "cell_ranger", "seurat_v3", "seurat_v3_paper"], optional, default = "seurat_v3",
            Only taken into account if you use the dataset for fine-tuning the model.
            Choose the flavor for identifying highly variable genes. 
            For the dispersion based methods in their default workflows, 
            Seurat passes the cutoffs whereas Cell Ranger passes n_top_genes.
        use_batch_labels: Bool, default = False
            Whether to use batch labels. Defaults to False.

        Returns
        -------
        Dataset
            The processed dataset.
        """
 
        self.check_data_validity(adata, gene_column_name, use_batch_labels)
        self.gene_column_name = gene_column_name
        self.adata = adata
        if fine_tuning:
            # Preprocess the dataset and select `N_HVG` highly variable genes for downstream analysis.
            sc.pp.normalize_total(self.adata, target_sum=1e4)
            sc.pp.log1p(self.adata)

            # highly variable genes
            sc.pp.highly_variable_genes(self.adata, n_top_genes=n_top_genes, flavor=flavor)
            self.adata = self.adata[:, self.adata.var['highly_variable']]

        adata.var["id_in_vocab"] = [ self.vocab[gene] if gene in self.vocab else -1 for gene in adata.var[self.gene_column_name] ]
        adata = adata[:, adata.var["id_in_vocab"] >= 0]

        # Binning will be applied after tokenization. A possible way to do is to use the unified way of binning in the data collator.

        self.vocab.set_default_index(self.vocab["<pad>"])
        genes = adata.var[self.gene_column_name].tolist()
        gene_ids = np.array(self.vocab(genes), dtype=int)
        count_matrix = (adata.X if isinstance(adata.X, np.ndarray) else adata.X.A)

        # gene vocabulary ids
        if gene_ids is None:
            gene_ids = np.array(adata.var["id_in_vocab"])
        assert np.all(gene_ids >= 0)

        if use_batch_labels:
            batch_ids = np.array(adata.obs["batch_id"].tolist())

        dataset = Dataset(
            count_matrix, gene_ids, self.vocab, self.config, batch_ids if use_batch_labels else None
        )
        return dataset


    def check_data_validity(self, adata: AnnData, gene_column_name: str, use_batch_labels: bool) -> None:
        """Checks if the data is eligible for processing by the scGPT model  

        Parameters
        ----------
        data : AnnData
            The AnnData object containing the data to be validated. 
        gene_column_name : str
            The name of the column containing gene names.
        use_batch_labels : str
            Wheter to use batch labels.

        Raises
        ------
        KeyError
            If the data is missing column names.
        """
        self.check_rna_data_validity(adata, gene_column_name)

        if use_batch_labels:
            if not "batch_id" in adata.obs:
                message = "Data must have the 'obs' key 'batch_id' to be processed by the scGPT model."
                LOGGER.error(message)
                raise KeyError(message)