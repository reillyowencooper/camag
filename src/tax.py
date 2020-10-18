import os, subprocess, shutil, argparse
import pandas as pd
from collections import Counter

def fetch_args(parser):
    parser.set_defaults(func = main)
    parser.set_defaults(program = "contig-tax")
    parser.add_argument('--input_mag', help = "Path to predicted CDS file for MAG")
    parser.add_argument('--search_db', default = "databases/swissprot/swissprot", help = "Path to MMSeqs SwissProt DB")
    parser.add_argument('--output_dir', help = "Directory to place output files")
    parser.add_argument('--tmp_dir', default= "tmp", help = "Directory to store temporary files")

def main(args):
    if not os.path.exists(args["tmp_dir"]):
        os.mkdir(args["tmp_dir"])
    magtax = MagTaxonomy(args["output_dir"])
    magtax.run(args["input_mag"],
               args["search_db"],
               args["tmp_dir"])
    

class MagTaxonomy(object):
    '''Taxonomically classifies individual contigs within a MAG.
    ORFs within a MAG are individually searched against the SwissProt database using MMseqs2.
    The most common Phylum-level classification for all identified ORFs is used as the contig classification.
    NOTE: Probably should do this as a weighted mean based on the # of ORFs in a contig, since some are larger.
    
    This uses Phylum-level classification because many high-quality MAGs tend to have a misplaced phylum-level cluster,
    as per Chen et al. 2020
    '''
    def __init__(self, output_dir):
        '''Inits
        output_dir: str
            Directory to store results
        '''
        self.output_dir = output_dir
        
    def create_mag_db(self, mag_aa, mag_name, tmp_dir):
        '''Turns input .faa into MMseqs DB.
        mag_aa: str
            Path to file containing predicted MAG amino acids
        mag_name: str
            Name of MAG of interest
        tmp_dir: str
            Directory to store temporary files (in this case, the MMseqs db)
        '''
        createdb_cmd = ['mmseqs', 'createdb', mag_aa, os.path.join(tmp_dir, mag_name + "_db")]
        subprocess.run(createdb_cmd)
        
    def get_contig_taxonomy(self, mag_db, mag_name, search_db, magtax_db, tmp_dir, output_file):
        '''Gets contig taxonomy using MMseqs and generates results TSV
        mag_db: str
            Path to MAG MMseqs DB
        mag_name: str
            Name of MAG of interest
        search_db: str
            Path to MMseqs search DB - usually using SwissProt
        magtax_db: str
            Path to output DB of MAG taxonomic classifications
        tmp_dir: str
            Directory to store temporary files
        output_file: str
            Path to output summarized taxonomic classifications
        '''
        gettax_cmd = ['mmseqs', 'taxonomy', mag_db, search_db, magtax_db, tmp_dir, '--merge-query', 1, '--remove-tmp-files', '--tax-lineage', 1]
        tsv_cmd = ['mmseqs', 'createtsv', mag_db, magtax_db, output_file]
        subprocess.run(gettax_cmd)
        subprocess.run(tsv_cmd)
        
    def parse_contig_taxonomy(self, mag_tax_tsv):
        '''Cleans the MMseqs TSV, filters unclassified CDS.
        mag_tax_tsv: str
            Path to taxonomy TSV generated by MMseqs
        Returns:
        tax
            DataFrame with CDS-level taxonomy
        '''
        tax = pd.read_csv(mag_tax_tsv, sep = '\t', header=None, names=['Contig','Acc','Cat','LCA','Full Tax'])
        tax = tax.join(tax['Full Tax'].str.split(';', expand = True)).drop(['Acc', 'Cat', 'Full Tax'], axis = 1)
        tax[['Contig Name','ORF']] = tax['Contig'].str.rsplit('_', 1, expand=True)
        tax = tax.drop(['Contig'], axis = 1)
        tax = tax.drop(tax.iloc[:, 10:36], axis = 1)
        tax = tax[tax['LCA'] != 'unclassified']
        return tax
    
    def get_contig_consensus_tax(self, clean_mag_tax_df):
        '''Generates phylum-level consensus taxonomy for each contig in a MAG.
        clean_mag_tax_df:
            DataFrame with CDS-level taxonomy
        Returns:
        tax_df
            DataFrame with each contig's consensus taxonomic rank, the
            most common phylum-level identity across that contig's CDS
        '''
        contig_tax = {}
        contig_names = clean_mag_tax_df['Contig Name'].unique().tolist()
        for contig in contig_names:
            contig_taxlist = []
            for index, row in clean_mag_tax_df.iterrows():
                if row['Contig Name'] == contig:
                    rowlist = [item for item in row.tolist() if item is not None]
                    for item in rowlist:
                        if item.startswith('p_'):
                            contig_taxlist.append(item)
            contig_phylum = Counter(contig_taxlist).most_common(1)[0][0]
            contig_tax[contig] = contig_phylum
        tax_df = pd.DataFrame(list(contig_tax.items()), columns = ['Contig', 'Phylum'])
        return tax_df
    
    def identify_erroneous_contigs(self, contig_tax_df):
        '''Finds contigs that do not match contig consensus taxonomy.
        contig_tax_df:
            Dataframe with each contig's consensus taxonomic rank
        Returns:
        erroneous_contigs:
            Subset of contig_tax_df only containing contigs not matching consensus
        '''
        phylum_ids = contig_tax_df['Phylum'].tolist()
        most_common_phylum = Counter(phylum_ids).most_common(1)[0][0]
        erroneous_contigs = phylum_ids[phylum_ids['Phylum'] != most_common_phylum]
        return erroneous_contigs
    
    def write_taxonomy_df(self, tax_df, output_file):
        '''Writes consensus and erroneous DataFrames to file.'''
        tax_df.to_csv(output_file, index = False, header = True)
        
    def remove_tmp_files(self, tmp_dir):
        '''Removes the temporary directory and all files inside.'''
        shutil.rmtree(tmp_dir)
        
    def run(self, mag_aa, 
            search_db, 
            tmp_dir):
        '''Runs MagTaxonomy on a given input MAG.'''
        mag_name = os.path.splitext(os.path.basename(mag_aa))[0]
        mag_db = os.path.join(tmp_dir, mag_name + "_db")
        mag_taxdb = os.path.join(tmp_dir, mag_name + "_tax")
        tax_tsv = os.path.join(tmp_dir, mag_name + "_taxonomy.tsv")
        contig_tax_outloc = os.path.join(self.output_dir, mag_name + "_taxonomy.tsv")
        erroneous_contig_outloc = os.path.join(self.output_dir, "suspicious_taxonomy.tsv")
        print('Creating MMseqs2 database from MAG')
        self.create_mag_db(mag_aa,
                           mag_name,
                           tmp_dir)
        print('Identifying contig taxonomy')
        self.get_contig_taxonomy(mag_db,
                                 mag_name,
                                 search_db,
                                 mag_taxdb,
                                 tmp_dir,
                                 tax_tsv)
        contig_taxonomy = self.parse_contig_taxonomy(tax_tsv)
        consensus_taxonomy = self.get_contig_consensus_tax(contig_taxonomy)
        self.write_taxonomy_df(contig_tax_outloc)
        self.write_taxonomy_df(erroneous_contig_outloc)
        self.remove_tmp_files(tmp_dir)