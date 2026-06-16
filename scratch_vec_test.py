import sqlite3
import sqlite_vec

def test_sqlite_vec():
    print("Testing sqlite-vec installation...")
    # Initialize connection
    conn = sqlite3.connect(":memory:")
    
    # Enable extension loading
    conn.enable_load_extension(True)
    
    # Load sqlite-vec extension
    sqlite_vec.load(conn)
    
    # Check loaded version
    version = conn.execute("SELECT vec_version()").fetchone()[0]
    print(f"sqlite-vec version: {version}")
    
    # Create a vector table for testing
    # dimensions = 3 for this test
    conn.execute("""
        CREATE VIRTUAL TABLE vec_test USING vec0(
            embedding float[3]
        );
    """)
    
    # Insert some vectors
    import json
    vectors = [
        (1, [1.0, 0.0, 0.0]),
        (2, [0.0, 1.0, 0.0]),
        (3, [0.0, 0.0, 1.0])
    ]
    
    for rowid, vec in vectors:
        conn.execute(
            "INSERT INTO vec_test(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(vec))
        )
    
    # Query vectors (KNN)
    query_vec = [1.0, 0.1, 0.0]
    results = conn.execute(
        """
        SELECT rowid, distance 
        FROM vec_test 
        WHERE embedding MATCH ? 
        ORDER BY distance 
        LIMIT 2
        """, 
        (sqlite_vec.serialize_float32(query_vec),)
    ).fetchall()
    
    print("KNN Results for [1.0, 0.1, 0.0]:")
    for rowid, dist in results:
        print(f" - Row {rowid}: distance {dist:.4f}")

if __name__ == "__main__":
    test_sqlite_vec()
