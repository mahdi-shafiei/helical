import logging
import numpy as np
from anndata import AnnData
from torch.utils.data import DataLoader
from pathlib import Path
import scanpy as sc
from tqdm import tqdm
import torch
from accelerate import Accelerator
from helical.models.uce.uce_config import UCEConfig
from helical.models.base_models import HelicalRNAModel
from helical.models.uce.uce_utils import get_ESM2_embeddings, get_positions, get_protein_embeddings_idxs, load_model, prepare_expression_counts_file
from helical.utils.downloader import Downloader
from helical.models.uce.uce_dataset import UCEDataset
from helical.models.uce.gene_embeddings import load_gene_embeddings_adata

LOGGER = logging.getLogger(__name__)
class UCE(HelicalRNAModel):
    """Universal Cell Embedding Model. This model reads in single-cell RNA-seq data and outputs gene embeddings. 
        This model particularly uses protein-embeddings generated by ESM2. 
        Currently we support human and macaque species but you can add your own species by providing the protein embeddings.

        Example
        -------
        >>> from helical import UCE, UCEConfig
        >>> from datasets import load_dataset
        >>> from helical.utils import get_anndata_from_hf_dataset
        >>> import anndata as ad
        >>> configurer=UCEConfig(batch_size=10)
        >>> uce = UCE(configurer=configurer)
        >>> hf_dataset = load_dataset("helical-ai/yolksac_human",split="train[:25%]", trust_remote_code=True, download_mode="reuse_cache_if_exists")
        >>> ann_data = get_anndata_from_hf_dataset(hf_dataset)
        >>> dataset = uce.process_data(ann_data[:100])
        >>> embeddings = uce.get_embeddings(dataset)

        Parameters
        ----------
        configurer : UCEConfig, optional, default = default_configurer
            The model configuration.

        Returns
        -------
        None

        Notes
        -----
        The Universal Cell Embedding Papers has been published on `BioRxiv <https://www.biorxiv.org/content/10.1101/2023.11.28.568918v1>`_ and it is built on top of `SATURN <https://www.nature.com/articles/s41592-024-02191-z>`_ published in Nature.
        """
    default_configurer = UCEConfig()

    def __init__(self, configurer: UCEConfig = default_configurer) -> None:    
        super().__init__()
        self.config = configurer.config

        downloader = Downloader()
        for file in self.config["list_of_files_to_download"]:
            downloader.download_via_name(file)

        self.model_dir = self.config['model_path'].parent
        self.device = self.config["device"]
        self.embeddings = get_ESM2_embeddings(self.config["token_file_path"], self.config["token_dim"])
        self.model =  load_model(self.config['model_path'], self.config, self.embeddings)
        self.model = self.model.eval().to(self.device)

        if self.config["accelerator"] or self.device=='cuda':
            self.accelerator = Accelerator(project_dir=self.model_dir)#, cpu=self.config["accelerator"]["cpu"])
            self.model = self.accelerator.prepare(self.model)
        else:
            self.accelerator = None
        LOGGER.info(f"Model finished initializing.")

    def process_data(self, 
                     adata: AnnData, 
                     gene_names: str = "index",
                     name = "test",
                     filter_genes_min_cell: int = None
                     ) -> UCEDataset:
        """Processes the data for the Universal Cell Embedding model

        Parameters 
        ----------
        adata : AnnData
            The AnnData object containing the data to be processed. 
            The UCE model requires the gene expression data as input and the gene symbols as variable names (i.e. as adata.var_names).
        gene_names: str, optional, default = "index"
            The name of the column in the AnnData object that contains the gene symbols.
            By default, the index of the AnnData object is used.
            If another column is specified, that column will be set as the index of the AnnData object.
        name: str, optional, default = "test"
            The name of the dataset. Needed for when slicing AnnData objects for train and validation datasets.
        filter_genes_min_cell: int, default = None
            Filter threshold that defines how many times a gene should occur in all the cells.

        Returns
        -------
        UCEDataset
            Inherits from Dataset class.
        """
        


        self.ensure_rna_data_validity(adata, gene_names)

        if gene_names != "index":
            adata.var.index = adata.var[gene_names]

        files_config = {
            "spec_chrom_csv_path": self.model_dir / "species_chrom.csv",
            "protein_embeddings_dir": self.model_dir / "protein_embeddings/",
            "offset_pkl_path": self.model_dir / "species_offsets.pkl"
        }
        
        ## TODO : Remove double downloads. This is required since metaflow might not have stored the files in the right location and the files might have dissapeared. The downloader should check if the file already exists.
        downloader = Downloader()
        for k,file in files_config.items():
            downloader.download_via_name(file)

        if filter_genes_min_cell is not None:
            sc.pp.filter_genes(adata, min_cells=filter_genes_min_cell)
            # sc.pp.filter_cells(ad, min_genes=25)
        ##Filtering out the Expression Data That we do not have in the protein embeddings
        filtered_adata, species_to_all_gene_symbols = load_gene_embeddings_adata(adata=adata,
                                                                        species=[self.config["species"]],
                                                                        embedding_model=self.config["gene_embedding_model"],
                                                                        embeddings_path=Path(files_config["protein_embeddings_dir"]))
        # TODO: What about hv_genes? See orig.
        gene_expression = adata.X.toarray()

        name = name
        gene_expression_folder_path = "./"
        prepare_expression_counts_file(gene_expression, name, gene_expression_folder_path)
        
        # shapes dictionary
        num_cells = filtered_adata.X.shape[0]
        num_genes = filtered_adata.X.shape[1]
        shapes_dict = {name: (num_cells, num_genes)}

        pe_row_idxs = get_protein_embeddings_idxs(files_config["offset_pkl_path"], self.config["species"], species_to_all_gene_symbols, filtered_adata)
        dataset_chroms, dataset_start = get_positions(Path(files_config["spec_chrom_csv_path"]), self.config["species"], filtered_adata)

        if not (len(dataset_chroms) == len(dataset_start) == num_genes == pe_row_idxs.shape[0]): 
            LOGGER.error(f'Invalid input dimensions for the UCEDataset! ' 
                        f'dataset_chroms: {len(dataset_chroms)}, '
                        f'dataset_start: {len(dataset_start)}, '
                        f'num_genes: {num_genes}, '
                        f'pe_row_idxs.shape[0]: {pe_row_idxs.shape[0]}')
            raise AssertionError
        
        dataset = UCEDataset(sorted_dataset_names = [name],
                             shapes_dict = shapes_dict,
                             model_config = self.config,
                             expression_counts_path = gene_expression_folder_path,
                             dataset_to_protein_embeddings = pe_row_idxs,
                             datasets_to_chroms = dataset_chroms,
                             datasets_to_starts = dataset_start
                             ) 
        LOGGER.info(f'Successfully prepared the UCE Dataset.')
        return dataset

    def get_embeddings(self, dataset: UCEDataset) -> np.array:
        """Gets the gene embeddings from the UCE model

        Parameters
        ----------
        dataset : UCEDataSet
            The Dataset object containing the processed data

        Returns
        -------
        np.array
            The gene embeddings in the form of a numpy array
        """
     
        batch_size = self.config["batch_size"]
        dataloader = DataLoader(dataset, 
                                batch_size=batch_size, 
                                shuffle=False,
                                collate_fn=dataset.collator_fn,
                                num_workers=0)
        

        if self.accelerator is not None:
            dataloader = self.accelerator.prepare(dataloader)


        # disable progress bar if not the main process
        if self.accelerator is not None:
            pbar = tqdm(dataloader, disable=not self.accelerator.is_local_main_process)
        else:
            pbar = tqdm(dataloader)
        
        LOGGER.info(f"Inference started")
        dataset_embeds = []
        
        # disabling gradient calculation for inference
        with torch.no_grad():
            for batch in pbar:
                batch_sentences, mask, idxs = batch[0], batch[1], batch[2]
                batch_sentences = batch_sentences.permute(1, 0)
                if self.config["multi_gpu"]:
                    batch_sentences = self.model.module.pe_embedding(batch_sentences.long())
                else:
                    batch_sentences = self.model.pe_embedding(batch_sentences.long())
                batch_sentences = torch.nn.functional.normalize(batch_sentences, dim=2)  # normalize token outputs
                _, embedding = self.model.forward(batch_sentences, mask=mask)
                
                # Fix for duplicates in last batch
                if self.accelerator is not None:
                    self.accelerator.wait_for_everyone()
                    embeddings = self.accelerator.gather_for_metrics((embedding))
                    if self.accelerator.is_main_process:
                        dataset_embeds.append(embeddings.detach().cpu().numpy())
                else:
                    dataset_embeds.append(embedding.detach().cpu().numpy())
        embeddings = np.vstack(dataset_embeds)
        return embeddings

    