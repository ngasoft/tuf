#!/usr/bin/env python

"""
<Program Name>
  test_key_revocation.py

<Author>
  Vladimir Diaz.

<Started>
  April 28, 2016.

<Copyright>
  See LICENSE for licensing information.

<Purpose>
  Integration test that verifies top-level roles are updated after all of their
  keys have been revoked. There are unit tests in 'test_repository_tool.py'
  that verify key and role revocation of specific roles, but these should be
  expanded to verify key revocations over the span of multiple snapshots of the
  repository.   
  
  The 'unittest_toolbox.py' module was created to provide additional testing
  tools, such as automatically deleting temporary files created in test cases.
  For more information on the additional testing tools, see
  'tests/unittest_toolbox.py'.
"""

# Help with Python 3 compatibility, where the print statement is a function, an
# implicit relative import is invalid, and the '/' operator performs true
# division.  Example:  print 'hello world' raises a 'SyntaxError' exception.
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import os
import time
import shutil
import copy
import tempfile
import logging
import random
import subprocess
import sys

# 'unittest2' required for testing under Python < 2.7.
if sys.version_info >= (2, 7):
  import unittest

else:
  import unittest2 as unittest 

import tuf
import tuf.log
import tuf.conf
import tuf.roledb
import tuf.keydb
import tuf.repository_tool as repo_tool
import tuf.unittest_toolbox as unittest_toolbox
import tuf.client.updater as updater
import six

logger = logging.getLogger('tuf.test_key_revocation')
repo_tool.disable_console_log_messages()


class TestKeyRevocation(unittest_toolbox.Modified_TestCase):

  @classmethod
  def setUpClass(cls):
    # setUpClass() is called before tests in an individual class are executed.
    
    # Create a temporary directory to store the repository, metadata, and target
    # files.  'temporary_directory' must be deleted in TearDownModule() so that
    # temporary files are always removed, even when exceptions occur. 
    cls.temporary_directory = tempfile.mkdtemp(dir=os.getcwd())
    
    # Launch a SimpleHTTPServer (serves files in the current directory).  Test
    # cases will request metadata and target files that have been pre-generated
    # in 'tuf/tests/repository_data', which will be served by the
    # SimpleHTTPServer launched here.  The test cases of
    # 'test_key_revocation.py' assume the pre-generated metadata files have a
    # specific structure, such as a delegated role, three target files, five
    # key files, etc.
    cls.SERVER_PORT = random.randint(30000, 45000)
    command = ['python', 'simple_server.py', str(cls.SERVER_PORT)]
    cls.server_process = subprocess.Popen(command, stderr=subprocess.PIPE)
    logger.info('\n\tServer process started.')
    logger.info('\tServer process id: '+str(cls.server_process.pid))
    logger.info('\tServing on port: '+str(cls.SERVER_PORT))
    cls.url = 'http://localhost:'+str(cls.SERVER_PORT) + os.path.sep

    # NOTE: Following error is raised if a delay is not applied:
    # <urlopen error [Errno 111] Connection refused>
    time.sleep(1)



  @classmethod 
  def tearDownClass(cls):
    # tearDownModule() is called after all the tests have run.
    # http://docs.python.org/2/library/unittest.html#class-and-module-fixtures
   
    # Remove the temporary repository directory, which should contain all the
    # metadata, targets, and key files generated for the test cases.
    shutil.rmtree(cls.temporary_directory)
    
    # Kill the SimpleHTTPServer process.
    if cls.server_process.returncode is None:
      logger.info('\tServer process '+str(cls.server_process.pid)+' terminated.')
      cls.server_process.kill()



  def setUp(self):
    # We are inheriting from custom class.
    unittest_toolbox.Modified_TestCase.setUp(self)
  
    # Copy the original repository files provided in the test folder so that
    # any modifications made to repository files are restricted to the copies.
    # The 'repository_data' directory is expected to exist in 'tuf.tests/'.
    original_repository_files = os.path.join(os.getcwd(), 'repository_data') 
    temporary_repository_root = \
      self.make_temp_directory(directory=self.temporary_directory)
  
    # The original repository, keystore, and client directories will be copied
    # for each test case. 
    original_repository = os.path.join(original_repository_files, 'repository')
    original_keystore = os.path.join(original_repository_files, 'keystore')
    original_client = os.path.join(original_repository_files, 'client')

    self.repository_name = 'defaultrepo' # the testrepo's name

    # Save references to the often-needed client repository directories.
    # Test cases need these references to access metadata and target files. 
    self.repository_directory = \
      os.path.join(temporary_repository_root, 'repository')
    self.keystore_directory = \
      os.path.join(temporary_repository_root, 'keystore')
    self.client_directory = os.path.join(temporary_repository_root, 'client')
    self.client_metadata = os.path.join(self.client_directory, 'metadata')
    self.client_metadata_current = os.path.join(
        self.client_metadata, self.repository_name, 'current')
    self.client_metadata_previous = os.path.join(
        self.client_metadata, self.repository_name, 'previous')

    # Copy the original 'repository', 'client', and 'keystore' directories
    # to the temporary repository the test cases can use.
    shutil.copytree(original_repository, self.repository_directory)
    shutil.copytree(original_client, self.client_directory)
    shutil.copytree(original_keystore, self.keystore_directory)

    # 'path/to/tmp/repository' -> 'localhost:8001/tmp/repository'. 
    repository_basepath = self.repository_directory[len(os.getcwd()):]
    url_prefix = \
      'http://localhost:' + str(self.SERVER_PORT) + repository_basepath 
    
    # Setting 'tuf.conf.repository_directory' with the temporary client
    # directory copied from the original repository files.
    tuf.conf.repository_directory = self.client_directory 
    


    # Creating a repository instance.  The test cases will use this client
    # updater to refresh metadata, fetch target files, etc.
    self.repository_updater = updater.Updater('testupdater')


    # Need to override pinned.json mirrors for testing. /:
    # Point it to the right URL with the randomly selected port generated in
    # this test setup.
    mirrors = self.repository_updater.pinned_metadata['repositories'][
        'defaultrepo']['mirrors']

    for i in range(0, len(mirrors)):
      if '<DETERMINED_IN_TEST_SETUP>' in mirrors[i]:
        mirrors[i] = mirrors[i].replace(
            '<DETERMINED_IN_TEST_SETUP>', str(url_prefix))

    self.repository_updater.pinned_metadata['repositories']['defaultrepo'][
        'mirrors'] = mirrors


    # Metadata role keys are needed by the test cases to make changes to the
    # repository (e.g., adding a new target file to 'targets.json' and then
    # requesting a refresh()).
    self.role_keys = _load_role_keys(self.keystore_directory)



  def tearDown(self):
    # We are inheriting from custom class.
    unittest_toolbox.Modified_TestCase.tearDown(self)
    tuf.roledb.clear_roledb(clear_all=True)
    tuf.keydb.clear_keydb(clear_all=True) 



  # UNIT TESTS.
  def test_timestamp_key_revocation(self):
    # First verify that the Timestamp role is properly signed.  Calling
    # refresh() should not raise an exception.
    self.repository_updater.refresh()

    # There should only be one key for Timestamp.  Store the keyid to later
    # verify that it has been revoked.
    timestamp_roleinfo = tuf.roledb.get_roleinfo('timestamp', self.repository_name) 
    timestamp_keyid = timestamp_roleinfo['keyids']
    self.assertEqual(len(timestamp_keyid), 1)

    # Remove 'timestamp_keyid' and add a new key.  Verify that the client
    # detects the removal and addition of keys to the Timestamp role.
    repository = repo_tool.load_repository(self.repository_directory)
    repository.timestamp.remove_verification_key(self.role_keys['timestamp']['public'])
    repository.timestamp.add_verification_key(self.role_keys['snapshot']['public'])
    
    # Root, Snapshot, and Timestamp must be rewritten.  Root must be written
    # because the timestamp key has changed; Snapshot, because  Root has
    # changed, and ...
    repository.root.load_signing_key(self.role_keys['root']['private'])
    repository.snapshot.load_signing_key(self.role_keys['snapshot']['private'])
    repository.timestamp.load_signing_key(self.role_keys['snapshot']['private'])
    repository.write()
   

    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # The client performs a refresh of top-level metadata to get the latest
    # changes.
    self.repository_updater.refresh()
    
    # Verify that the client is able to recognize that a new set of keys have
    # been added to the Timestamp role.
    # First, has 'timestamp_keyid' been removed?
    timestamp_roleinfo = tuf.roledb.get_roleinfo('timestamp', self.repository_name) 
    self.assertTrue(timestamp_keyid not in timestamp_roleinfo['keyids'])

    # Second, is Timestamp's new key correct?  The new key should be Snapshot's.

    self.assertEqual(len(timestamp_roleinfo['keyids']), 1)
    snapshot_roleinfo = tuf.roledb.get_roleinfo('snapshot', self.repository_name)
    self.assertEqual(timestamp_roleinfo['keyids'], snapshot_roleinfo['keyids']) 
  
  
  
  def test_snapshot_key_revocation(self):
    # First verify that the Snapshot role is properly signed.  Calling
    # refresh() should not raise an exception.
    self.repository_updater.refresh()

    # There should only be one key for Snapshot.  Store the keyid to later
    # verify that it has been revoked.
    snapshot_roleinfo = tuf.roledb.get_roleinfo('snapshot', self.repository_name) 
    snapshot_keyid = snapshot_roleinfo['keyids']
    self.assertEqual(len(snapshot_keyid), 1)


    # Remove 'snapshot_keyid' and add a new key.  Verify that the client
    # detects the removal and addition of keys to the Snapshot role.
    repository = repo_tool.load_repository(self.repository_directory)
    repository.snapshot.remove_verification_key(self.role_keys['snapshot']['public'])
    repository.snapshot.add_verification_key(self.role_keys['timestamp']['public'])
    
    # Root, Snapshot, and Timestamp must be rewritten.  Root must be written
    # because the timestamp key has changed; Snapshot, because  Root has
    # changed, and Timesamp, because it must sign its metadata with a new key.
    repository.root.load_signing_key(self.role_keys['root']['private'])
    # Note: we added Timetamp's key to the Snapshot role.
    repository.snapshot.load_signing_key(self.role_keys['timestamp']['private'])
    repository.timestamp.load_signing_key(self.role_keys['timestamp']['private'])
    repository.write()
   

    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # The client performs a refresh of top-level metadata to get the latest
    # changes.
    self.repository_updater.refresh()
    
    # Verify that the client is able to recognize that a new set of keys have
    # been added to the Snapshot role.
    # First, has 'snapshot_keyid' been removed?
    snapshot_roleinfo = tuf.roledb.get_roleinfo('snapshot', self.repository_name) 
    self.assertTrue(snapshot_keyid not in snapshot_roleinfo['keyids'])

    # Second, is Snapshot's new key correct?  The new key should be
    # Timestamp's.
    self.assertEqual(len(snapshot_roleinfo['keyids']), 1)
    timestamp_roleinfo = tuf.roledb.get_roleinfo('timestamp', self.repository_name)
    self.assertEqual(snapshot_roleinfo['keyids'], timestamp_roleinfo['keyids']) 





  def test_targets_key_revocation(self):
    # First verify that the Targets role is properly signed.  Calling
    # refresh() should not raise an exception.
    self.repository_updater.refresh()

    # There should only be one key for Targets.  Store the keyid to later
    # verify that it has been revoked.
    targets_roleinfo = tuf.roledb.get_roleinfo('targets', self.repository_name) 
    targets_keyid = targets_roleinfo['keyids']
    self.assertEqual(len(targets_keyid), 1)

    # Remove 'targets_keyid' and add a new key.  Verify that the client
    # detects the removal and addition of keys to the Targets role.
    repository = repo_tool.load_repository(self.repository_directory)
    repository.targets.remove_verification_key(self.role_keys['targets']['public'])
    repository.targets.add_verification_key(self.role_keys['timestamp']['public'])
    
    # Root, Snapshot, and Timestamp must be rewritten.  Root must be written
    # because the timestamp key has changed; Snapshot, because  Root has
    # changed, and Timestamp because it must sign its metadata with a new key.
    repository.root.load_signing_key(self.role_keys['root']['private'])
    # Note: we added Timetamp's key to the Targets role.
    repository.targets.load_signing_key(self.role_keys['timestamp']['private'])
    repository.snapshot.load_signing_key(self.role_keys['snapshot']['private'])
    repository.timestamp.load_signing_key(self.role_keys['timestamp']['private'])
    repository.write()
   

    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # The client performs a refresh of top-level metadata to get the latest
    # changes.
    self.repository_updater.refresh()
    
    # Verify that the client is able to recognize that a new set of keys have
    # been added to the Targets role.
    # First, has 'targets_keyid' been removed?
    targets_roleinfo = tuf.roledb.get_roleinfo('targets', self.repository_name) 
    self.assertTrue(targets_keyid not in targets_roleinfo['keyids'])

    # Second, is Targets's new key correct?  The new key should be
    # Timestamp's.
    self.assertEqual(len(targets_roleinfo['keyids']), 1)
    timestamp_roleinfo = tuf.roledb.get_roleinfo('timestamp', self.repository_name)
    self.assertEqual(targets_roleinfo['keyids'], timestamp_roleinfo['keyids']) 
  
 

  def test_root_key_revocation(self):
    """
    Three-part test:
    1: Revoke the old root key (the only key at the time), add a new root key,
       and try updating. Client will reject because client expects only the
       old key.
    2: Sign with the old key in addition to the new key. Client should
       accept and revoke the old key.
    3: Sign with only the new key. Client should accept.
    (Sidenote: if there is a threshold of multiple root keys, the race
    security issue here is prevented.)
    """

    # First verify that the Root role is properly signed.  Calling
    # refresh() should not raise an exception.
    self.repository_updater.refresh()
    
    # There should only be one key for Root.  Store the keyid to later verify
    # that it has been revoked.
    root_roleinfo = tuf.roledb.get_roleinfo('root', self.repository_name) 
    root_keyid = root_roleinfo['keyids']
    self.assertEqual(len(root_keyid), 1)


    # Test 1: revoke, sign with new only, expect client rejection

    # Remove 'root_keyid' and add a new key.  Verify that the client detects
    # the removal and addition of keys to the Root file. (We'll use the
    # targets key in this test for convenience. Obviously, one should not
    # do that in the real world.)
    repository = repo_tool.load_repository(self.repository_directory)
    repository.root.remove_verification_key(self.role_keys['root']['public'])
    repository.root.add_verification_key(self.role_keys['targets']['public'])
    repository.root.load_signing_key(self.role_keys['targets']['private'])
    
    # Snapshot and Timestamp must also be rewritten: Snapshot, because Root has
    # changed, and Timestamp because it must note the new version of Snapshot.
    repository.snapshot.load_signing_key(self.role_keys['snapshot']['private'])
    repository.timestamp.load_signing_key(self.role_keys['timestamp']['private'])

    # Make sure new versions are written. Possibly unnecessary.
    repository.mark_dirty(['snapshot', 'timestamp', 'root'])
 
    # Root's version number = 2 after the following write().
    repository.write()
    
    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))
   
    # Note well: The client should reject the new Root file because the
    # repository has revoked the only Root key that the client trusts.
    try: 
      self.repository_updater.refresh()
    
    except tuf.NoWorkingMirrorError as exception:
      for mirror_exception in exception.mirror_errors.values():
        self.assertTrue(isinstance(mirror_exception, tuf.BadSignatureError))


    # Test 2: sign with old and new, expect client acceptance and key revocation

    # Load the previous Root signing key so that the the client can update
    # successfully.
    repository.root.load_signing_key(self.role_keys['root']['private'])
    # Make sure new version is written, and update snapshot so it holds the new
    # version/hash for root, and timestamp so it marks the new version of
    # snapshot. Probably unnecessary to mark root dirty (should be automatic).
    repository.mark_dirty(['root', 'snapshot', 'timestamp'])
    repository.write() 

    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # Root's version number = 3...
    # The client successfully performs a refresh of top-level metadata to get
    # the latest changes.
    self.repository_updater.refresh()
    self.assertEqual(self.repository_updater.repositories[
        'defaultrepo'].metadata['current']['root']['version'], 3)


    # Test 3: sign with only the new key, except client acceptance

    # Discontinue signing with the old root key now that the client has
    # successfully updated (note: the old Root key was revoked, but the
    # repository continued signing with it to allow the client to update).
    repository.root.unload_signing_key(self.role_keys['root']['private'])
    repository.write()
    
    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # Root's version number = 4...
    self.repository_updater.refresh() 

    # Verify that the client is able to recognize that a new set of keys have
    # been added to the Root role.
    # First, has 'root`_keyid' been removed?
    root_roleinfo = tuf.roledb.get_roleinfo('root', self.repository_name)
    self.assertTrue(root_keyid not in root_roleinfo['keyids'])

    # Second, is Root's new key correct?  The new key should be Targets's.
    self.assertEqual(len(root_roleinfo['keyids']), 1)
    targets_roleinfo = tuf.roledb.get_roleinfo('targets', self.repository_name)
    self.assertEqual(root_roleinfo['keyids'], targets_roleinfo['keyids']) 



def _load_role_keys(keystore_directory):
  
  # Populating 'self.role_keys' by importing the required public and private
  # keys of 'tuf/tests/repository_data/'.  The role keys are needed when
  # modifying the remote repository used by the test cases in this unit test.

  # The pre-generated key files in 'repository_data/keystore' are all encrypted with
  # a 'password' passphrase.
  EXPECTED_KEYFILE_PASSWORD = 'password'

  # Store and return the cryptography keys of the top-level roles, including 1
  # delegated role.
  role_keys = {}

  root_key_file = os.path.join(keystore_directory, 'root_key')
  targets_key_file = os.path.join(keystore_directory, 'targets_key')
  snapshot_key_file = os.path.join(keystore_directory, 'snapshot_key')
  timestamp_key_file = os.path.join(keystore_directory, 'timestamp_key')
  delegation_key_file = os.path.join(keystore_directory, 'delegation_key')

  role_keys = {'root': {}, 'targets': {}, 'snapshot': {}, 'timestamp': {},
               'role1': {}}

  # Import the top-level and delegated role public keys.
  role_keys['root']['public'] = \
    repo_tool.import_rsa_publickey_from_file(root_key_file+'.pub')
  role_keys['targets']['public'] = \
    repo_tool.import_ed25519_publickey_from_file(targets_key_file + '.pub')
  role_keys['snapshot']['public'] = \
    repo_tool.import_ed25519_publickey_from_file(snapshot_key_file + '.pub')
  role_keys['timestamp']['public'] = \
      repo_tool.import_ed25519_publickey_from_file(timestamp_key_file + '.pub')
  role_keys['role1']['public'] = \
      repo_tool.import_ed25519_publickey_from_file(delegation_key_file + '.pub')

  # Import the private keys of the top-level and delegated roles.
  role_keys['root']['private'] = \
    repo_tool.import_rsa_privatekey_from_file(root_key_file, 
                                              EXPECTED_KEYFILE_PASSWORD)
  role_keys['targets']['private'] = \
    repo_tool.import_ed25519_privatekey_from_file(targets_key_file,
                                              EXPECTED_KEYFILE_PASSWORD)
  role_keys['snapshot']['private'] = \
    repo_tool.import_ed25519_privatekey_from_file(snapshot_key_file,
                                              EXPECTED_KEYFILE_PASSWORD)
  role_keys['timestamp']['private'] = \
    repo_tool.import_ed25519_privatekey_from_file(timestamp_key_file,
                                              EXPECTED_KEYFILE_PASSWORD)
  role_keys['role1']['private'] = \
    repo_tool.import_ed25519_privatekey_from_file(delegation_key_file,
                                              EXPECTED_KEYFILE_PASSWORD)

  return role_keys



if __name__ == '__main__':
  unittest.main()
