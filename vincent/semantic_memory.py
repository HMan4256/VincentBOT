"""Local multilingual sentence embeddings and a content-versioned SQLite cache."""
import hashlib
import threading

MODEL = 'onnx-community/paraphrase-multilingual-MiniLM-L12-v2-ONNX'
REVISION = 'd4c06bf0d7680171ac30042a1387e1fdb7a90021'
MODEL_KEY = MODEL + '@' + REVISION + ':quantized-mean-v1'
FILES = ('onnx/model_quantized.onnx', 'tokenizer.json')


def download_model():
    from huggingface_hub import hf_hub_download
    return [hf_hub_download(MODEL, name, revision=REVISION) for name in FILES]


class SemanticMemory:
    def __init__(self, store):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        self.store = store
        self.lock = threading.Lock()
        paths = [hf_hub_download(MODEL, name, revision=REVISION, local_files_only=True) for name in FILES]
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(paths[0], sess_options=options, providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(paths[1])
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding()

    def encode(self, texts):
        import numpy as np
        if not texts:return np.empty((0,384), dtype=np.float32)
        with self.lock:
            encoded = self.tokenizer.encode_batch(texts)
            ids = np.asarray([e.ids for e in encoded], dtype=np.int64)
            mask = np.asarray([e.attention_mask for e in encoded], dtype=np.int64)
            inputs = {'input_ids': ids, 'attention_mask': mask, 'token_type_ids': np.zeros_like(ids)}
            inputs = {i.name:inputs[i.name] for i in self.session.get_inputs()}
            token_embeddings = self.session.run(None, inputs)[0]
            vectors = (token_embeddings * mask[:,:,None]).sum(axis=1) / np.maximum(mask.sum(axis=1,keepdims=True),1)
            vectors = vectors / np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12)
            return vectors.astype(np.float32)

    def scores(self, rows, query):
        import numpy as np
        if not rows or not query.strip():return {}
        ids = [r['id'] for r in rows]
        hashes = {r['id']:hashlib.sha256(r['content'].encode('utf8')).hexdigest() for r in rows}
        cached={}
        with self.store.lock:
            for start in range(0,len(ids),500):
                chunk=ids[start:start+500]
                sql='SELECT memory_id,model,content_hash,vector FROM memory_embeddings WHERE memory_id IN ('+','.join('?' for _ in chunk)+')'
                cached.update({r['memory_id']:r for r in self.store.db.execute(sql,chunk).fetchall()})
        vectors={}
        missing=[]
        for row in rows:
            record=cached.get(row['id'])
            if record and record['model']==MODEL_KEY and record['content_hash']==hashes[row['id']] and len(record['vector'])==384*4:
                vectors[row['id']]=np.frombuffer(record['vector'],dtype=np.float32)
            else:missing.append(row)
        # Index only records already selected by SQL scope and user preferences.
        for start in range(0,len(missing),32):
            batch=missing[start:start+32]
            encoded=self.encode([r['content'] for r in batch])
            with self.store.transaction() as db:
                for row,vector in zip(batch,encoded):
                    # A correction or deletion may have occurred while encoding.
                    current=db.execute('SELECT content,status FROM memories WHERE id=?',(row['id'],)).fetchone()
                    if current and current['status']=='active' and current['content']==row['content']:
                        db.execute('INSERT OR REPLACE INTO memory_embeddings(memory_id,model,content_hash,vector) VALUES(?,?,?,?)',(row['id'],MODEL_KEY,hashes[row['id']],vector.tobytes()))
                        vectors[row['id']]=vector
        query_vector=self.encode([query])[0]
        return {mid:float(np.dot(vector,query_vector)) for mid,vector in vectors.items() if mid in ids}


if __name__ == '__main__':
    for path in download_model():print(path)
