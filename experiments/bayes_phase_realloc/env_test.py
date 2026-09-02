import os
import multiprocessing as mp


def child(_):
    return os.environ.get('TEST_VAR', 'NOT_SET')


if __name__ == '__main__':
    print('Before pool:', os.environ.get('TEST_VAR', 'NOT_SET'))
    os.environ['TEST_VAR'] = 'set_at_runtime'
    print('After set: ', os.environ.get('TEST_VAR', 'NOT_SET'))

    pool = mp.Pool(2)
    results = pool.map(child, [None]*4)
    pool.close()
    pool.join()
    print('Worker results:', results)
